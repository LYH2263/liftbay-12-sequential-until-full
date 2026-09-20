"""Elevator dispatch: same-direction preference + floor distance; reject if car full."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class CarState:
    car_id: int
    floor: int
    direction: str  # "up" | "down" | "idle"
    load: int
    capacity: int


@dataclass(frozen=True)
class CallRequest:
    call_id: int
    floor: int
    direction: str  # desired travel after boarding
    passengers: int = 1


@dataclass(frozen=True)
class ScoreResult:
    car_id: int
    score: float
    accepted: bool
    reason: str


SAME_DIR_BONUS = 40.0
IDLE_BONUS = 20.0
DISTANCE_WEIGHT = 5.0


def score_car(car: CarState, call: CallRequest) -> ScoreResult:
    if car.load + call.passengers > car.capacity:
        return ScoreResult(car.car_id, -1e9, False, "轿厢满员")

    distance = abs(car.floor - call.floor)
    score = 100.0 - distance * DISTANCE_WEIGHT

    if car.direction == "idle":
        score += IDLE_BONUS
    elif car.direction == call.direction:
        # approaching or already going same way
        if car.direction == "up" and car.floor <= call.floor:
            score += SAME_DIR_BONUS
        elif car.direction == "down" and car.floor >= call.floor:
            score += SAME_DIR_BONUS
        else:
            score -= 15.0  # same dir but already passed
    else:
        score -= 25.0

    return ScoreResult(car.car_id, score, True, "ok")


def pick_car(cars: list[CarState], call: CallRequest) -> ScoreResult | None:
    results = [score_car(c, call) for c in cars]
    accepted = [r for r in results if r.accepted]
    if not accepted:
        return None
    return max(accepted, key=lambda r: r.score)


def congestion_by_floor(calls: list[CallRequest]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for c in calls:
        counts[c.floor] = counts.get(c.floor, 0) + c.passengers
    return counts


@dataclass(frozen=True)
class AssignedCall:
    call_id: int
    car_id: int
    score: float


@dataclass(frozen=True)
class SequenceResult:
    assigned: list[AssignedCall]
    cars: dict[int, CarState]  # 前缀成功后各轿厢的最新状态（载荷已累加）
    stopped_call_id: int | None
    reason: str  # 队列全部派完时为空串


def assign_one(
    states: dict[int, CarState], call: CallRequest
) -> AssignedCall | None:
    """按当前轿厢状态为单笔评分派车；成功则就地累加载荷并返回派车结果。

    全部轿厢接不下时返回 None，states 保持不变。
    """
    best = pick_car(list(states.values()), call)
    if best is None:
        return None
    car = states[best.car_id]
    states[best.car_id] = replace(
        car,
        load=car.load + call.passengers,
        floor=call.floor,
        direction=call.direction,
    )
    return AssignedCall(call.call_id, best.car_id, best.score)


def dispatch_sequence(
    cars: list[CarState], calls: list[CallRequest]
) -> SequenceResult:
    """按 calls 给定顺序逐笔评分派车（前缀成功，不整批回滚）。

    每成功一笔，载荷立即累加到所选轿厢，轿厢楼层/方向同步到该笔，
    后续笔基于最新状态重新评分；某笔全部轿厢都接不下时立即停止，
    已成功的笔保留，该笔及其后的笔不再处理。
    """
    states = {c.car_id: c for c in cars}
    assigned: list[AssignedCall] = []
    for call in calls:
        item = assign_one(states, call)
        if item is None:
            reason = (
                f"呼梯 #{call.call_id}（{call.floor} 层，{call.passengers} 人）"
                "全部轿厢接不下，停止连续派工"
            )
            return SequenceResult(assigned, states, call.call_id, reason)
        assigned.append(item)
    return SequenceResult(assigned, states, None, "")
