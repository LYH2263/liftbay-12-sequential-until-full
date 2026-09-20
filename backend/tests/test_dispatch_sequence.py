"""连续派工：前缀成功、超载即停、回放只含成功笔。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.router import api_router
from app.database import Base, get_db
from app.models.models import Building, CallTicket, DispatchLog, ElevatorCar


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(api_router, prefix="/api")

    def _get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


def _seed(db):
    b = Building(name="测试楼", floors=10)
    db.add(b)
    db.flush()
    car = ElevatorCar(building_id=b.id, label="T1", floor=1, direction="idle", load=0, capacity=5)
    db.add(car)
    # 三笔 waiting：第二笔 3 人在第一笔 3 人后必超载（容量 5）
    c1 = CallTicket(building_id=b.id, floor=3, direction="up", passengers=3, status="waiting")
    c2 = CallTicket(building_id=b.id, floor=6, direction="up", passengers=3, status="waiting")
    c3 = CallTicket(building_id=b.id, floor=9, direction="up", passengers=1, status="waiting")
    db.add_all([c1, c2, c3])
    db.commit()
    return car, c1, c2, c3


def test_sequence_prefix_committed_third_untouched(client, db_session):
    car, c1, c2, c3 = _seed(db_session)

    resp = client.post("/api/dispatch/sequence")
    assert resp.status_code == 200
    data = resp.json()

    # 仅第一笔成功，停在第二笔
    assert [a["call_id"] for a in data["assigned"]] == [c1.id]
    assert data["assigned"][0]["car_id"] == car.id
    assert data["stopped_call_id"] == c2.id
    assert "接不下" in data["reason"]

    db_session.expire_all()
    t1, t2, t3 = (db_session.get(CallTicket, i) for i in (c1.id, c2.id, c3.id))

    # 第一笔已落库为 assigned，载荷累加；第二、三笔仍 waiting
    assert t1.status == "assigned"
    assert t1.assigned_car_id == car.id
    assert t1.score
    assert t2.status == "waiting"
    assert t2.assigned_car_id is None
    assert t3.status == "waiting"
    assert t3.assigned_car_id is None
    assert db_session.get(ElevatorCar, car.id).load == 3

    # 回放只含已成功的笔，且无 rejected 日志
    logs = db_session.scalars(select(DispatchLog).order_by(DispatchLog.id)).all()
    assert [l.call_id for l in logs] == [c1.id]
    assert all(l.car_id is not None for l in logs)


def test_sequence_empty_queue_reports_reason(client, db_session):
    b = Building(name="空楼", floors=10)
    db_session.add(b)
    db_session.commit()

    resp = client.post("/api/dispatch/sequence")
    assert resp.status_code == 200
    data = resp.json()
    assert data["assigned"] == []
    assert data["stopped_call_id"] is None
    assert data["reason"] == "等待队列为空"


def test_sequence_all_fit_when_capacity_allows(client, db_session):
    b = Building(name="大楼", floors=10)
    db_session.add(b)
    db_session.flush()
    car = ElevatorCar(building_id=b.id, label="T9", floor=1, direction="idle", load=0, capacity=10)
    db_session.add(car)
    db_session.flush()
    tickets = [
        CallTicket(building_id=b.id, floor=f, direction="up", passengers=1, status="waiting")
        for f in (2, 3, 4)
    ]
    db_session.add_all(tickets)
    db_session.commit()

    resp = client.post("/api/dispatch/sequence")
    data = resp.json()
    assert resp.status_code == 200
    assert [a["call_id"] for a in data["assigned"]] == [t.id for t in tickets]
    assert data["stopped_call_id"] is None
    assert db_session.get(ElevatorCar, car.id).load == 3
