from app.services.dispatch_engine import (
    CallRequest,
    CarState,
    dispatch_sequence,
    pick_car,
    score_car,
)


def test_reject_when_full():
    car = CarState(1, 5, "idle", load=8, capacity=8)
    call = CallRequest(1, 5, "up", passengers=1)
    r = score_car(car, call)
    assert r.accepted is False
    assert "满员" in r.reason


def test_same_direction_beats_far_idle():
    cars = [
        CarState(1, 2, "up", load=1, capacity=10),
        CarState(2, 12, "idle", load=0, capacity=10),
    ]
    call = CallRequest(9, 4, "up", 1)
    best = pick_car(cars, call)
    assert best is not None
    assert best.car_id == 1


def test_closer_idle_wins_when_opposite():
    cars = [
        CarState(1, 10, "down", load=0, capacity=10),
        CarState(2, 3, "idle", load=0, capacity=10),
    ]
    call = CallRequest(3, 2, "up", 1)
    best = pick_car(cars, call)
    assert best is not None
    assert best.car_id == 2


def test_sequence_stops_on_second_overload_keeps_prefix():
    # 单轿厢容量 5：第一笔 3 人可接，第二笔 3 人接不下，第三笔不应处理
    cars = [CarState(1, 1, "idle", load=0, capacity=5)]
    calls = [
        CallRequest(10, 3, "up", passengers=3),
        CallRequest(11, 6, "up", passengers=3),
        CallRequest(12, 9, "up", passengers=1),
    ]
    result = dispatch_sequence(cars, calls)

    assert [a.call_id for a in result.assigned] == [10]
    assert result.stopped_call_id == 11
    assert "接不下" in result.reason
    # 载荷按成功笔数累加，轿厢状态同步到第一笔
    assert result.cars[1].load == 3
    assert result.cars[1].floor == 3
    assert result.cars[1].direction == "up"


def test_sequence_load_accumulates_across_successes():
    cars = [CarState(1, 1, "idle", load=0, capacity=5)]
    calls = [
        CallRequest(1, 2, "up", passengers=2),
        CallRequest(2, 3, "up", passengers=2),
    ]
    result = dispatch_sequence(cars, calls)

    assert [a.call_id for a in result.assigned] == [1, 2]
    assert result.stopped_call_id is None
    assert result.cars[1].load == 4
