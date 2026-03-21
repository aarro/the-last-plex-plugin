import { useEffect, useState } from "react";

const API = "";
const TABS = ["All", "Matched", "Unmatched"];

async function fetchVideos() {
  const res = await fetch(`${API}/api/videos`);
  const data = await res.json();
  return data.videos ?? [];
}

export default function VideoList({ collectionsKey, onAddToCollection }) {
  const [open, setOpen] = useState(false);
  const [videos, setVideos] = useState(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState("All");
  const [expandedTags, setExpandedTags] = useState(new Set());

  // Refresh when collections change (after Save). If open, re-fetch in-place.
  // If closed, reset to null so the next Show triggers a fresh fetch.
  useEffect(() => {
    if (collectionsKey === 0) return; // skip initial mount
    if (open) {
      setLoading(true);
      fetchVideos()
        .then(setVideos)
        .catch(() => setVideos([]))
        .finally(() => setLoading(false));
    } else {
      setVideos(null);
    }
  }, [collectionsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = async () => {
    if (!open && videos === null) {
      setLoading(true);
      try {
        setVideos(await fetchVideos());
      } catch {
        setVideos([]);
      } finally {
        setLoading(false);
      }
    }
    setOpen((v) => !v);
  };

  const toggleTags = (id) => {
    setExpandedTags((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
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
              {filtered.map((v) => {
                const tagsExpanded = expandedTags.has(v.id);
                const tags = v.tags ?? [];
                const visibleTags = tagsExpanded ? tags : tags.slice(0, 5);
                const hiddenCount = tags.length - 5;

                return (
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
                      {tags.length > 0 && (
                        <div className="video-tags">
                          {visibleTags.map((tag) => (
                            <span
                              key={tag}
                              className="tag-chip-sm"
                              title={`Add collection rule for "${tag}"`}
                              onClick={() => onAddToCollection?.(tag)}
                            >
                              {tag}
                            </span>
                          ))}
                          {!tagsExpanded && hiddenCount > 0 && (
                            <span
                              className="tag-more"
                              onClick={() => toggleTags(v.id)}
                            >
                              +{hiddenCount} more
                            </span>
                          )}
                          {tagsExpanded && tags.length > 5 && (
                            <span
                              className="tag-more"
                              onClick={() => toggleTags(v.id)}
                            >
                              show less
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
