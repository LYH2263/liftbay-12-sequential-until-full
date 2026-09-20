from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.models import Building, CallTicket, DispatchLog, ElevatorCar


def _setup_three_waiting_with_tight_capacity():
    """三笔 waiting：第二笔在第一笔落库并累加载荷后，所有轿厢都接不下。"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        b = Building(name="测试楼", floors=18)
        db.add(b)
        db.flush()
        db.add_all([
            ElevatorCar(building_id=b.id, label="T1", floor=1, direction="idle",
                        load=0, capacity=10),
            ElevatorCar(building_id=b.id, label="T2", floor=1, direction="idle",
                        load=10, capacity=10),
        ])
        db.flush()
        c1 = CallTicket(building_id=b.id, floor=5, direction="up",
                        passengers=8, status="waiting")
        c2 = CallTicket(building_id=b.id, floor=6, direction="up",
                        passengers=3, status="waiting")
        c3 = CallTicket(building_id=b.id, floor=7, direction="up",
                        passengers=1, status="waiting")
        db.add_all([c1, c2, c3])
        db.commit()
        return b.id, c1.id, c2.id, c3.id
    finally:
        db.close()


def test_sequence_second_overflow_keeps_prefix_persisted():
    bid, id1, id2, id3 = _setup_three_waiting_with_tight_capacity()
    with TestClient(app) as client:
        resp = client.post("/api/dispatch/sequence", json={"building_id": bid})

    assert resp.status_code == 200
    data = resp.json()
    # 本次只成功第一笔，并在第二笔处停下
    assert [item["call_id"] for item in data["assigned"]] == [id1]
    assert data["assigned"][0]["car_id"]
    assert data["stopped_call_id"] == id2
    assert str(id2) in data["stop_reason"]
    assert "接不下" in data["stop_reason"]

    db = SessionLocal()
    try:
        t1, t2, t3 = (db.get(CallTicket, i) for i in (id1, id2, id3))
        # 第一笔已落库为 assigned，第二、三笔仍 waiting（前缀成功，不整批回滚）
        assert t1.status == "assigned"
        assert t1.assigned_car_id is not None
        assert t1.score != ""
        assert t2.status == "waiting"
        assert t2.assigned_car_id is None
        assert t3.status == "waiting"
        assert t3.assigned_car_id is None

        cars = db.query(ElevatorCar).order_by(ElevatorCar.id).all()
        # 载荷按已成功的笔数累加：8 人入 T1；T2 维持满员
        assert cars[0].load == 8
        assert cars[1].load == 10

        # 回放只含已成功的第一笔
        logs = db.query(DispatchLog).order_by(DispatchLog.id).all()
        assert [log.call_id for log in logs] == [id1]
        assert all(log.car_id is not None for log in logs)
    finally:
        db.close()

    with TestClient(app) as client:
        replay = client.get("/api/replay").json()
        assert [row["call_id"] for row in replay] == [id1]
        cars = client.get("/api/cars").json()
        assert cars[0]["load"] == 8
        calls = client.get("/api/calls").json()
        by_id = {c["id"]: c for c in calls}
        assert by_id[id1]["status"] == "assigned"
        assert by_id[id2]["status"] == "waiting"
        assert by_id[id3]["status"] == "waiting"


def test_sequence_dispatches_all_when_fitting():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        b = Building(name="全装得下楼", floors=18)
        db.add(b)
        db.flush()
        db.add(ElevatorCar(building_id=b.id, label="E1", floor=1,
                           direction="idle", load=0, capacity=10))
        db.flush()
        c1 = CallTicket(building_id=b.id, floor=2, direction="up",
                        passengers=2, status="waiting")
        c2 = CallTicket(building_id=b.id, floor=3, direction="up",
                        passengers=3, status="waiting")
        db.add_all([c1, c2])
        db.commit()
        bid, id1, id2 = b.id, c1.id, c2.id
    finally:
        db.close()

    with TestClient(app) as client:
        resp = client.post("/api/dispatch/sequence", json={"building_id": bid})
    assert resp.status_code == 200
    data = resp.json()
    assert [item["call_id"] for item in data["assigned"]] == [id1, id2]
    assert data["stopped_call_id"] is None
    assert "全部派完" in data["stop_reason"]

    db = SessionLocal()
    try:
        car = db.query(ElevatorCar).one()
        assert car.load == 5  # 载荷按成功笔数累加 2+3
    finally:
        db.close()


def test_sequence_empty_queue_is_ok():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as client:
        resp = client.post("/api/dispatch/sequence", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["assigned"] == []
    assert data["stopped_call_id"] is None
