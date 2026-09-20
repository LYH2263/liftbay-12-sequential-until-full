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


def test_sequence_prefix_success_when_second_overflows():
    # 第一笔 8 人派入 A1（载荷累加到 8）；第二笔 3 人所有轿厢都接不下 → 停；
    # 第三笔不再处理，且成功的只有第一笔（允许前缀成功，不回滚）。
    cars = [
        CarState(1, 1, "idle", load=0, capacity=10),
        CarState(2, 1, "idle", load=10, capacity=10),
    ]
    calls = [
        CallRequest(11, 5, "up", passengers=8),
        CallRequest(12, 6, "up", passengers=3),
        CallRequest(13, 7, "up", passengers=1),
    ]
    result = dispatch_sequence(cars, calls)

    assert [a.call_id for a in result.assignments] == [11]
    assert result.assignments[0].car_id == 1
    assert result.stopped_call_id == 12
    assert "12" in result.stop_reason
    assert "接不下" in result.stop_reason


def test_sequence_dispatches_all_when_everything_fits():
    cars = [CarState(1, 1, "idle", load=0, capacity=10)]
    calls = [
        CallRequest(1, 2, "up", passengers=2),
        CallRequest(2, 4, "up", passengers=3),
    ]
    result = dispatch_sequence(cars, calls)

    assert [a.call_id for a in result.assignments] == [1, 2]
    assert result.stopped_call_id is None
    assert "全部派完" in result.stop_reason


def test_sequence_load_accumulates_across_successes():
    # 第一笔 6 人后 A1 余 4，第二笔 5 人接不下：验证载荷按成功笔数累加后才评分
    cars = [CarState(1, 1, "idle", load=0, capacity=10)]
    calls = [
        CallRequest(1, 3, "up", passengers=6),
        CallRequest(2, 5, "up", passengers=5),
    ]
    result = dispatch_sequence(cars, calls)

    assert [a.call_id for a in result.assignments] == [1]
    assert result.stopped_call_id == 2
