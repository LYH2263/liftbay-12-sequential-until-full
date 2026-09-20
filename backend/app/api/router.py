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
    DispatchSequenceRequest,
    LogOut,
    SequenceDispatchItem,
    SequenceDispatchOut,
)
from app.services.dispatch_engine import (
    CallRequest,
    CarState,
    congestion_by_floor,
    dispatch_sequence,
    pick_car,
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
    cars = [
        CarState(c.id, c.floor, c.direction, c.load, c.capacity) for c in car_rows
    ]
    call = CallRequest(ticket.id, ticket.floor, ticket.direction, ticket.passengers)
    best = pick_car(cars, call)
    if best is None:
        db.add(DispatchLog(call_id=ticket.id, car_id=None, detail="全部轿厢满员，拒绝派工"))
        ticket.status = "rejected"
        db.commit()
        db.refresh(ticket)
        raise HTTPException(409, "无可用轿厢（满员）")
    car = db.get(ElevatorCar, best.car_id)
    assert car
    ticket.status = "assigned"
    ticket.assigned_car_id = car.id
    ticket.score = f"{best.score:.1f}"
    car.load += ticket.passengers
    car.floor = ticket.floor
    car.direction = ticket.direction
    db.add(
        DispatchLog(
            call_id=ticket.id,
            car_id=car.id,
            detail=f"派予 {car.label}，评分 {best.score:.1f}（同向/距离综合）",
        )
    )
    db.commit()
    db.refresh(ticket)
    return ticket


@api_router.post("/dispatch/sequence", response_model=SequenceDispatchOut)
def dispatch_sequence_bulk(
    body: DispatchSequenceRequest, db: Session = Depends(get_db)
):
    """按当前 waiting 顺序（id 升序，即登记先后）连续派工。

    前缀成功的呼梯立即落库为 assigned 并累加轿厢载荷；遇到第一笔全部轿厢
    接不下的呼梯即停，该笔及其后的呼梯保持 waiting（不做整批回滚）。
    回放日志只记录成功的笔。
    """
    waiting_q = select(CallTicket).where(CallTicket.status == "waiting")
    if body.building_id is not None:
        waiting_q = waiting_q.where(CallTicket.building_id == body.building_id)
    else:
        # 未指定楼栋时，只处理队首呼梯所属楼栋，避免跨楼栋混派
        first = db.scalars(waiting_q.order_by(CallTicket.id).limit(1)).first()
        if first is None:
            return SequenceDispatchOut(
                assigned=[], stopped_call_id=None,
                stop_reason="waiting 队列为空，无可派呼梯",
            )
        waiting_q = waiting_q.where(CallTicket.building_id == first.building_id)
    waiting = list(db.scalars(waiting_q.order_by(CallTicket.id)).all())

    if not waiting:
        return SequenceDispatchOut(
            assigned=[], stopped_call_id=None,
            stop_reason="waiting 队列为空，无可派呼梯",
        )

    building_id = waiting[0].building_id
    car_rows = db.scalars(
        select(ElevatorCar).where(ElevatorCar.building_id == building_id)
    ).all()
    cars = [CarState(c.id, c.floor, c.direction, c.load, c.capacity) for c in car_rows]
    calls = [CallRequest(t.id, t.floor, t.direction, t.passengers) for t in waiting]

    result = dispatch_sequence(cars, calls)

    tickets_by_id = {t.id: t for t in waiting}
    cars_by_id = {c.id: c for c in car_rows}
    items: list[SequenceDispatchItem] = []
    for a in result.assignments:
        ticket = tickets_by_id[a.call_id]
        car = cars_by_id[a.car_id]
        ticket.status = "assigned"
        ticket.assigned_car_id = car.id
        ticket.score = f"{a.score:.1f}"
        car.load += ticket.passengers
        car.floor = ticket.floor
        car.direction = ticket.direction
        db.add(
            DispatchLog(
                call_id=ticket.id,
                car_id=car.id,
                detail=f"连续派工派予 {car.label}，评分 {a.score:.1f}（同向/距离综合）",
            )
        )
        items.append(
            SequenceDispatchItem(call_id=ticket.id, car_id=car.id, score=ticket.score)
        )

    db.commit()
    return SequenceDispatchOut(
        assigned=items,
        stopped_call_id=result.stopped_call_id,
        stop_reason=result.stop_reason,
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
