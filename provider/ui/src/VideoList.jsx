import { useState } from "react";

const API = "";
const TABS = ["All", "Matched", "Unmatched"];

export default function VideoList() {
  const [open, setOpen] = useState(false);
  const [videos, setVideos] = useState(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState("All");

  const toggle = async () => {
    if (!open && videos === null) {
      setLoading(true);
      try {
        const res = await fetch(`${API}/api/videos`);
        const data = await res.json();
        setVideos(data.videos ?? []);
      } catch {
        setVideos([]);
      } finally {
        setLoading(false);
      }
    }
    setOpen((v) => !v);
  };

  const filtered = (videos ?? []).filter((v) => {
    if (tab === "Matched" && !v.matched) return false;
    if (tab === "Unmatched" && v.matched) return false;
    if (search) {
      const q = search.toLowerCase();
      return v.title.toLowerCase().includes(q) || v.channel.toLowerCase().includes(q);
    }
    return true;
  });

  const total = videos?.length ?? 0;
  const matched = videos?.filter((v) => v.matched).length ?? 0;
  const unmatched = total - matched;

  return (
    <section>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <h2 style={{ margin: 0 }}>Videos</h2>
        <button className="btn-ghost btn-sm" onClick={toggle} disabled={loading}>
          {loading ? "Loading…" : open ? "Hide" : "Show"}
        </button>
        {videos !== null && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            {total} total — {matched} matched, {unmatched} unmatched
          </span>
        )}
      </div>

      {open && videos !== null && (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
            <input
              type="text"
              placeholder="Search title or channel…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ flex: 1, minWidth: 160 }}
            />
            <div style={{ display: "flex", gap: 4 }}>
              {TABS.map((t) => (
                <button
                  key={t}
                  className={tab === t ? "btn-primary btn-sm" : "btn-ghost btn-sm"}
                  onClick={() => setTab(t)}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {filtered.length === 0 ? (
            <p className="empty">No videos match.</p>
          ) : (
            <div className="video-grid">
              {filtered.map((v) => (
                <div key={v.id} className="video-card">
                  {v.thumbnail && (
                    <img
                      src={v.thumbnail}
                      alt=""
                      className="video-thumb"
                      loading="lazy"
                    />
                  )}
                  <div className="video-info">
                    <div className="video-title" title={v.title}>{v.title}</div>
                    <div className="video-meta">
                      <span>{v.channel}</span>
                      {v.upload_date && <span>{v.upload_date}</span>}
                    </div>
                    {v.collections.length > 0 && (
                      <div className="video-collections">
                        {v.collections.map((c) => (
                          <span key={c} className="collection-badge">{c}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
