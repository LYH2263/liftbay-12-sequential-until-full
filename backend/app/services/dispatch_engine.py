"""Elevator dispatch: same-direction preference + floor distance; reject if car full."""

from __future__ import annotations

from dataclasses import dataclass


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


@dataclass(frozen=True)
class DispatchAssignment:
    call_id: int
    car_id: int
    score: float


@dataclass(frozen=True)
class SequenceDispatchResult:
    assignments: tuple[DispatchAssignment, ...]
    stopped_call_id: int | None
    stop_reason: str


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


def dispatch_sequence(
    cars: list[CarState], calls: list[CallRequest]
) -> SequenceDispatchResult:
    """按 calls 给定顺序逐笔用现网评分派车，载荷随成功笔数累加。

    遇到第一笔所有轿厢都接不下的呼梯即停止：之前的成功笔保留在
    assignments 中（允许前缀成功），该笔及之后的笔不再处理，由调用方
    继续保持 waiting。轿厢的位置/方向也随每笔成功更新，与单笔派工落库
    后的再评分口径一致。
    """
    states: dict[int, CarState] = {c.car_id: c for c in cars}
    assignments: list[DispatchAssignment] = []

    for call in calls:
        best = pick_car(list(states.values()), call)
        if best is None:
            return SequenceDispatchResult(
                tuple(assignments),
                call.call_id,
                f"呼梯 #{call.call_id}（{call.passengers} 人）全部轿厢接不下，停止连续派工",
            )
        cur = states[best.car_id]
        states[best.car_id] = CarState(
            cur.car_id,
            call.floor,
            call.direction,
            cur.load + call.passengers,
            cur.capacity,
        )
        assignments.append(
            DispatchAssignment(call.call_id, best.car_id, best.score)
        )

    if not assignments:
        reason = "waiting 队列为空，无可派呼梯"
    else:
        reason = "waiting 队列已全部派完"
    return SequenceDispatchResult(tuple(assignments), None, reason)
