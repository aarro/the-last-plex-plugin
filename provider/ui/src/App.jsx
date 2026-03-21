import { useCallback, useEffect, useState } from "react";
import Collections from "./Collections.jsx";
import DiscoverPanel from "./DiscoverPanel.jsx";

const API = "";

export default function App() {
  const [data, setData] = useState(null);
  const [videos, setVideos] = useState([]);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState(null);
  const [saving, setSaving] = useState(false);
  const [rescanning, setRescanning] = useState(false);

  const load = useCallback(async () => {
    const [colRes, vidRes] = await Promise.all([
      fetch(`${API}/api/collections`),
      fetch(`${API}/api/videos`),
    ]);
    setData(await colRes.json());
    setVideos((await vidRes.json()).videos ?? []);
    setDirty(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const setCollections = (collections) => {
    setData((d) => ({ ...d, collections }));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    setStatus(null);
    try {
      const res = await fetch(`${API}/api/collections`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ collections: data.collections }),
      });
      if (!res.ok) throw new Error(await res.text());
      const result = await res.json();
      setStatus({ type: "ok", msg: `Saved — ${result.matched} matched, ${result.unmatched} unmatched.` });
      await load();
    } catch (e) {
      setStatus({ type: "err", msg: `Save failed: ${e.message}` });
    } finally {
      setSaving(false);
    }
  };

  const rescan = async () => {
    setRescanning(true);
    setStatus(null);
    try {
      const res = await fetch(`${API}/api/rescan`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      const json = await res.json();
      const n = json.triggered_sections?.length ?? 0;
      setStatus({ type: n > 0 ? "ok" : "err", msg: n > 0 ? "Plex rescan triggered." : "Rescan failed — no YAMP-managed library found in Plex." });
    } catch (e) {
      setStatus({ type: "err", msg: `Rescan failed: ${e.message}` });
    } finally {
      setRescanning(false);
    }
  };

  const createCollectionFromTag = (tag) => {
    const newCollection = {
      name: tag.split(" ").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" "),
      rules: [{ field: "tags", values: [tag], match: "exact" }],
    };
    setCollections([...(data?.collections ?? []), newCollection]);
  };

  if (!data) return <div className="app"><p className="empty">Loading…</p></div>;

  return (
    <div className="app">
      <header>
        <h1><span>YAMP</span> — Collection Manager</h1>
        <div className="stats">
          <div className="stat">
            <div className="stat-value">{data.matched_count}</div>
            <div className="stat-label">Matched</div>
          </div>
          <div className="stat">
            <div className="stat-value">{data.unmatched_count}</div>
            <div className="stat-label">Unmatched</div>
          </div>
          <div className="stat">
            <div className="stat-value">{data.collections.length}</div>
            <div className="stat-label">Collections</div>
          </div>
        </div>
      </header>

      <div className="two-col">
        <div className="col-left">
          <Collections
            collections={data.collections}
            videos={videos}
            onChange={setCollections}
          />
        </div>
        <div className="col-right">
          <DiscoverPanel
            videos={videos}
            onAddToCollection={createCollectionFromTag}
          />
        </div>
      </div>

      <div className="action-bar">
        {status && (
          <span className={`status ${status.type}`}>{status.msg}</span>
        )}
        <button className="btn-ghost" onClick={rescan} disabled={rescanning}>
          {rescanning ? "Rescanning…" : "Rescan Plex"}
        </button>
        <button className="btn-primary" onClick={save} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save Changes"}
        </button>
      </div>
    </div>
  );
}
