import { useCallback, useEffect, useState } from "react";
import Collections from "./Collections.jsx";
import DiscoverPanel from "./DiscoverPanel.jsx";

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(await res.text());
  try {
    return await res.json();
  } catch {
    throw new Error(`Server returned invalid JSON (HTTP ${res.status} from ${res.url})`);
  }
}

export default function App() {
  const [data, setData] = useState(null);
  const [videos, setVideos] = useState([]);
  const [version, setVersion] = useState(null);
  const [status, setStatus] = useState(null);
  const [rescanning, setRescanning] = useState(false);
  const [fixingThumbs, setFixingThumbs] = useState(false);
  const [search, setSearch] = useState("");
  const [loadError, setLoadError] = useState(null);

  const load = useCallback(async () => {
    const [colData, vidData] = await Promise.all([fetchJson("/api/collections"), fetchJson("/api/videos")]);
    if (!Array.isArray(colData?.collections)) throw new Error("Unexpected response from /api/collections");
    if (!Array.isArray(vidData?.videos)) throw new Error("Unexpected response from /api/videos");
    setData(colData);
    setVideos(vidData.videos);
  }, []);

  useEffect(() => {
    load().catch((e) => setLoadError(e.message));
    fetchJson("/api/version")
      .then((d) => setVersion(d.version))
      .catch((e) => console.warn("Failed to fetch version:", e));
  }, [load]);

  const setCollections = (collections) => {
    setData((d) => ({ ...d, collections }));
  };

  const saveWithCollections = async (collections, makeMsg) => {
    setStatus(null);
    try {
      const result = await fetchJson("/api/collections", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ collections }),
      });
      const plexNote = result.plex_sync ? " Plex syncing in background." : "";
      setStatus({ type: "ok", msg: `${makeMsg(result)}${plexNote}` });
      try {
        await load();
      } catch (e) {
        console.error("Post-save reload failed:", e);
        setStatus({ type: "err", msg: `Saved, but failed to refresh (${e.message}) — please reload the page.` });
        return false;
      }
      return true;
    } catch (e) {
      setStatus({ type: "err", msg: `Save failed: ${e.message}` });
      return false;
    }
  };

  const rescan = async () => {
    setRescanning(true);
    setStatus(null);
    try {
      const json = await fetchJson("/api/rescan", { method: "POST" });
      const triggered = json.triggered_sections ?? [];
      const failed = json.failed_sections ?? [];
      const sectionLabel = (s) => {
        const label = s.title ?? s.id ?? null;
        if (!label) console.warn("Unexpected rescan section shape — no title or id:", s);
        return label ?? "unknown";
      };
      if (triggered.length > 0 && failed.length === 0) {
        const names = triggered.map(sectionLabel).join(", ");
        setStatus({ type: "ok", msg: `Rescan triggered for "${names}"` });
      } else if (triggered.length > 0) {
        const names = triggered.map(sectionLabel).join(", ");
        const failNames = failed.map(sectionLabel).join(", ");
        setStatus({ type: "err", msg: `Rescan triggered for "${names}". Failed: ${failNames}` });
      } else if (failed.length > 0) {
        const failNames = failed.map(sectionLabel).join(", ");
        setStatus({ type: "err", msg: `Rescan failed for all sections: ${failNames}` });
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
      const json = await fetchJson("/api/thumbnails/fix", { method: "POST" });
      const msg = `Thumbnails fixed: ${json.fixed} updated, ${json.failed} failed, ${json.skipped} skipped.`;
      setStatus({ type: json.failed > 0 ? "err" : "ok", msg });
    } catch (e) {
      setStatus({ type: "err", msg: `Fix thumbnails failed: ${e.message}` });
    } finally {
      setFixingThumbs(false);
    }
  };

  if (loadError)
    return (
      <div className="app">
        <p className="empty" style={{ color: "var(--danger)" }}>
          Failed to load: {loadError}
        </p>
      </div>
    );
  if (!data)
    return (
      <div className="app">
        <p className="empty">Loading…</p>
      </div>
    );

  return (
    <div className="app">
      <header>
        <div className="header-brand">
          <img src="/logo.svg" alt="YAMP" height="40" className="header-logo" />
        </div>
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
            onVideoSearch={setSearch}
            onSave={(updatedCollections) =>
              saveWithCollections(updatedCollections, (r) => `Saved — ${r.matched} matched, ${r.unmatched} unmatched`)
            }
          />
        </div>
        <div className="col-right">
          <DiscoverPanel videos={videos} search={search} onSearch={setSearch} />
        </div>
      </div>

      <div className="action-bar">
        {version && <span className="action-bar-version">{version}</span>}
        {status && <span className={`status ${status.type}`}>{status.msg}</span>}
        <button type="button" className="btn-ghost" onClick={fixThumbnails} disabled={fixingThumbs}>
          {fixingThumbs ? "Fixing…" : "Fix Thumbnails"}
        </button>
        <button
          type="button"
          className="btn-ghost"
          onClick={rescan}
          disabled={rescanning}
          title="Ask Plex to re-fetch metadata for all videos in YAMP-managed libraries"
        >
          {rescanning ? "Scanning…" : "Trigger Plex Scan"}
        </button>
      </div>
    </div>
  );
}
