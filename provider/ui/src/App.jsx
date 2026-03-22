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
  const [fixingThumbs, setFixingThumbs] = useState(false);

  const load = useCallback(async () => {
    const [colRes, vidRes] = await Promise.all([fetch(`${API}/api/collections`), fetch(`${API}/api/videos`)]);
    setData(await colRes.json());
    setVideos((await vidRes.json()).videos ?? []);
    setDirty(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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
      let result;
      try {
        result = await res.json();
      } catch {
        throw new Error("Server returned invalid response");
      }
      const artworkFails = Object.entries(result.artwork ?? {})
        .filter(([, v]) => !v.ok)
        .map(([k]) => k);
      const msg =
        `Saved — ${result.matched} matched, ${result.unmatched} unmatched.` +
        (artworkFails.length ? ` Artwork failed for: ${artworkFails.join(", ")}.` : "");
      setStatus({ type: artworkFails.length ? "err" : "ok", msg });
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
      let json;
      try {
        json = await res.json();
      } catch {
        throw new Error("Server returned invalid response");
      }
      const n = json.triggered_sections?.length ?? 0;
      if (n > 0) {
        setStatus({ type: "ok", msg: "Plex rescan triggered." });
      } else {
        setStatus({ type: "err", msg: "Rescan failed — no YAMP-managed library found in Plex." });
      }
    } catch (e) {
      setStatus({ type: "err", msg: `Rescan failed: ${e.message}` });
    } finally {
      setRescanning(false);
    }
  };

  const fixThumbnails = async () => {
    setFixingThumbs(true);
    setStatus(null);
    try {
      const res = await fetch(`${API}/api/thumbnails/fix`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      let json;
      try {
        json = await res.json();
      } catch {
        throw new Error("Server returned invalid response");
      }
      const msg = `Thumbnails fixed: ${json.fixed} updated, ${json.failed} failed, ${json.skipped} skipped.`;
      setStatus({ type: json.failed > 0 ? "err" : "ok", msg });
    } catch (e) {
      setStatus({ type: "err", msg: `Fix thumbnails failed: ${e.message}` });
    } finally {
      setFixingThumbs(false);
    }
  };

  if (!data)
    return (
      <div className="app">
        <p className="empty">Loading…</p>
      </div>
    );

  return (
    <div className="app">
      <header>
        <img src="/logo.svg" alt="YAMP" height="40" style={{ display: "block" }} />
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
          <Collections collections={data.collections} videos={videos} onChange={setCollections} />
        </div>
        <div className="col-right">
          <DiscoverPanel videos={videos} />
        </div>
      </div>

      <div className="action-bar">
        {status && <span className={`status ${status.type}`}>{status.msg}</span>}
        <button type="button" className="btn-ghost" onClick={fixThumbnails} disabled={fixingThumbs}>
          {fixingThumbs ? "Fixing…" : "Fix Thumbnails"}
        </button>
        <button type="button" className="btn-ghost" onClick={rescan} disabled={rescanning}>
          {rescanning ? "Rescanning…" : "Rescan Plex"}
        </button>
        <button type="button" className="btn-primary" onClick={save} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save Changes"}
        </button>
      </div>
    </div>
  );
}
