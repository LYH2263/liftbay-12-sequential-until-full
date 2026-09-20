from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Building, CallTicket, DispatchLog, ElevatorCar
from app.schemas.schemas import (
    BuildingOut,
    CallCreate,
    CallOut,
    CarOut,
    CongestionFloor,
    DispatchRequest,
    LogOut,
    SequenceAssignedOut,
    SequenceDispatchOut,
)
from app.services.dispatch_engine import (
    CallRequest,
    CarState,
    assign_one,
    congestion_by_floor,
)

api_router = APIRouter()


@api_router.get("/health")
def health():
    return {"status": "ok"}


@api_router.get("/buildings", response_model=list[BuildingOut])
def buildings(db: Session = Depends(get_db)):
    return db.scalars(select(Building).order_by(Building.id)).all()


@api_router.get("/cars", response_model=list[CarOut])
def cars(db: Session = Depends(get_db)):
    return db.scalars(select(ElevatorCar).order_by(ElevatorCar.id)).all()


@api_router.get("/calls", response_model=list[CallOut])
def calls(db: Session = Depends(get_db)):
    return db.scalars(select(CallTicket).order_by(CallTicket.id.desc())).all()


@api_router.post("/calls", response_model=CallOut)
def create_call(body: CallCreate, db: Session = Depends(get_db)):
    b = db.get(Building, body.building_id)
    if not b:
        raise HTTPException(404, "楼栋不存在")
    if body.floor > b.floors:
        raise HTTPException(400, "楼层超出")
    if body.direction not in ("up", "down"):
        raise HTTPException(400, "方向无效")
    ticket = CallTicket(
        building_id=body.building_id,
        floor=body.floor,
        direction=body.direction,
        passengers=body.passengers,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


@api_router.post("/dispatch", response_model=CallOut)
def dispatch(body: DispatchRequest, db: Session = Depends(get_db)):
    ticket = db.get(CallTicket, body.call_id)
    if not ticket:
        raise HTTPException(404, "呼梯不存在")
    if ticket.status != "waiting":
        raise HTTPException(400, "呼梯已处理")
    car_rows = db.scalars(
        select(ElevatorCar).where(ElevatorCar.building_id == ticket.building_id)
    ).all()
    states = {c.id: CarState(c.id, c.floor, c.direction, c.load, c.capacity) for c in car_rows}
    call = CallRequest(ticket.id, ticket.floor, ticket.direction, ticket.passengers)
    item = assign_one(states, call)
    if item is None:
        db.add(DispatchLog(call_id=ticket.id, car_id=None, detail="全部轿厢满员，拒绝派工"))
        ticket.status = "rejected"
        db.commit()
        db.refresh(ticket)
        raise HTTPException(409, "无可用轿厢（满员）")
    car = db.get(ElevatorCar, item.car_id)
    assert car
    state = states[car.id]
    ticket.status = "assigned"
    ticket.assigned_car_id = car.id
    ticket.score = f"{item.score:.1f}"
    car.load = state.load
    car.floor = state.floor
    car.direction = state.direction
    db.add(
        DispatchLog(
            call_id=ticket.id,
            car_id=car.id,
            detail=f"派予 {car.label}，评分 {item.score:.1f}（同向/距离综合）",
        )
    )
    db.commit()
    db.refresh(ticket)
    return ticket


@api_router.post("/dispatch/sequence", response_model=SequenceDispatchOut)
def dispatch_sequence_api(db: Session = Depends(get_db)):
    """按当前 waiting 队列（ID 升序）连续派工，允许前缀成功。

    每笔成功立即提交：呼梯置 assigned、所选轿厢载荷累加并写回放日志；
    遇到某笔全部轿厢接不下时停止，该笔及其后呼梯保持 waiting，
    回放中只包含本次已成功的笔。
    """
    tickets = db.scalars(
        select(CallTicket)
        .where(CallTicket.status == "waiting")
        .order_by(CallTicket.id)
    ).all()

    car_rows = db.scalars(select(ElevatorCar)).all()
    cars_by_id = {c.id: c for c in car_rows}
    states_by_building: dict[int, dict[int, CarState]] = defaultdict(dict)
    for c in car_rows:
        states_by_building[c.building_id][c.id] = CarState(
            c.id, c.floor, c.direction, c.load, c.capacity
        )

    assigned_out: list[SequenceAssignedOut] = []
    stopped_call_id: int | None = None
    reason = ""

    for ticket in tickets:
        states = states_by_building[ticket.building_id]
        call = CallRequest(ticket.id, ticket.floor, ticket.direction, ticket.passengers)
        item = assign_one(states, call)
        if item is None:
            stopped_call_id = ticket.id
            reason = (
                f"呼梯 #{ticket.id}（{ticket.floor} 层，{ticket.passengers} 人）"
                "全部轿厢接不下，停止连续派工；后续呼梯未处理"
            )
            break

        car = cars_by_id[item.car_id]
        state = states[car.id]
        ticket.status = "assigned"
        ticket.assigned_car_id = car.id
        ticket.score = f"{item.score:.1f}"
        car.load = state.load
        car.floor = state.floor
        car.direction = state.direction
        db.add(
            DispatchLog(
                call_id=ticket.id,
                car_id=car.id,
                detail=f"连续派工：派予 {car.label}，评分 {item.score:.1f}（同向/距离综合）",
            )
        )
        db.commit()  # 逐笔落库：已成功的前缀不随后续失败回滚
        assigned_out.append(
            SequenceAssignedOut(call_id=item.call_id, car_id=item.car_id, score=ticket.score)
        )

    if not tickets:
        reason = "等待队列为空"

    return SequenceDispatchOut(
        assigned=assigned_out, stopped_call_id=stopped_call_id, reason=reason
    )


@api_router.get("/replay", response_model=list[LogOut])
def replay(db: Session = Depends(get_db)):
    return db.scalars(select(DispatchLog).order_by(DispatchLog.id.desc())).all()


@api_router.get("/congestion", response_model=list[CongestionFloor])
def congestion(db: Session = Depends(get_db)):
    waiting = db.scalars(select(CallTicket).where(CallTicket.status == "waiting")).all()
    counts = congestion_by_floor(
        [CallRequest(c.id, c.floor, c.direction, c.passengers) for c in waiting]
    )
    return [
        CongestionFloor(floor=f, passengers=p)
        for f, p in sorted(counts.items(), key=lambda x: -x[1])
    ]
