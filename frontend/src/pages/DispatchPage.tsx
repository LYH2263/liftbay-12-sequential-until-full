import { useEffect, useState } from "react";
import { api } from "../api/client";
type Call = { id: number; floor: number; direction: string; passengers: number; status: string; score: string; assigned_car_id: number | null };
type SeqItem = { call_id: number; car_id: number; score: string };
type SeqResult = { assigned: SeqItem[]; stopped_call_id: number | null; stop_reason: string };
export default function DispatchPage() {
  const [rows, setRows] = useState<Call[]>([]);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const [seq, setSeq] = useState<SeqResult | null>(null);
  const reload = () => api<Call[]>("/calls").then(setRows);
  useEffect(() => { reload(); }, []);
  async function run(id: number) {
    setMsg(""); setErr(""); setSeq(null);
    try {
      const c = await api<Call>("/dispatch", { method: "POST", body: JSON.stringify({ call_id: id }) });
      setMsg(`呼梯 #${c.id} → 轿厢 ${c.assigned_car_id}，评分 ${c.score}`);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); reload(); }
  }
  async function runSequence() {
    setMsg(""); setErr(""); setSeq(null);
    try {
      const r = await api<SeqResult>("/dispatch/sequence", { method: "POST", body: JSON.stringify({}) });
      setSeq(r);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); reload(); }
  }
  const waiting = rows.filter(r => r.status === "waiting");
  return (<>
    <h2>派工</h2>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    {seq && (
      <div className={`seq-result ${seq.stopped_call_id !== null ? "err" : "ok"}`}>
        <div>
          本次已派：
          {seq.assigned.length
            ? seq.assigned.map(a => `#${a.call_id}→轿厢${a.car_id}（${a.score}）`).join("，")
            : "无"}
        </div>
        <div>{seq.stop_reason}{seq.stopped_call_id !== null ? "，其后呼梯仍 waiting" : ""}</div>
      </div>
    )}
    <div className="toolbar">
      <button onClick={runSequence} disabled={!waiting.length}>按 waiting 顺序连续派工</button>
      <span className="hint">从队首呼梯所属楼栋开始，逐笔评分派车；某笔全部轿厢接不下即停，已派的不回滚</span>
    </div>
    <table className="table"><thead><tr><th>呼梯</th><th>楼层</th><th>方向</th><th>人数</th><th></th></tr></thead>
    <tbody>{waiting.map(c => <tr key={c.id}><td>#{c.id}</td><td>{c.floor}</td><td>{c.direction}</td><td>{c.passengers}</td>
      <td><button onClick={() => run(c.id)}>评分派轿厢</button></td></tr>)}
      {!waiting.length && <tr><td colSpan={5}>暂无待派呼梯</td></tr>}
    </tbody></table>
  </>);
}
