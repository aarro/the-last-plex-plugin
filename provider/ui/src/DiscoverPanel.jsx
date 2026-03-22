import { useState } from "react";

export default function DiscoverPanel({ videos, onAddToCollection }) {
  const [search, setSearch] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [expandedTags, setExpandedTags] = useState(new Set());

  const toggleTags = (id) => {
    setExpandedTags((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  };

  const base = showAll ? videos : videos.filter((v) => !v.matched);

  const filtered = base.filter((v) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      v.title.toLowerCase().includes(q) ||
      v.channel.toLowerCase().includes(q) ||
      (v.tags ?? []).some((t) => t.toLowerCase().includes(q))
    );
  });

  const unmatchedCount = videos.filter((v) => !v.matched).length;

  return (
    <section>
      <h2>Discover</h2>

      <input
        type="text"
        placeholder="Search title, channel, or tags…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{ width: "100%", marginBottom: 10 }}
      />

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <button
          type="button"
          className={!showAll ? "btn-primary btn-sm" : "btn-ghost btn-sm"}
          onClick={() => setShowAll(false)}
        >
          Unmatched ({unmatchedCount})
        </button>
        <button
          type="button"
          className={showAll ? "btn-primary btn-sm" : "btn-ghost btn-sm"}
          onClick={() => setShowAll(true)}
        >
          All ({videos.length})
        </button>
        {search && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            {filtered.length} result{filtered.length !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {filtered.length === 0 ? (
        <p className="empty">{search ? "No videos match your search." : "All videos are matched!"}</p>
      ) : (
        <div className="discover-grid">
          {filtered.map((v) => {
            const tags = v.tags ?? [];
            const tagsExpanded = expandedTags.has(v.id);
            const visibleTags = tagsExpanded ? tags : tags.slice(0, 5);
            const hiddenCount = tags.length - 5;

            return (
              <div key={v.id} className="discover-card">
                {v.thumbnail && <img src={v.thumbnail} alt="" className="discover-thumb" loading="lazy" />}
                <div className="discover-info">
                  <div className="video-title" title={v.title}>
                    {v.title}
                  </div>
                  <div className="video-meta">
                    <span>{v.channel}</span>
                    {v.upload_date && <span>{v.upload_date}</span>}
                  </div>
                  {v.collections.length > 0 && (
                    <div className="video-collections">
                      {v.collections.map((c) => (
                        <span key={c} className="collection-badge">
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                  {tags.length > 0 && (
                    <div className="video-tags">
                      {visibleTags.map((tag) => (
                        <button
                          key={tag}
                          type="button"
                          className="tag-chip-sm"
                          title={`Add collection rule for "${tag}"`}
                          onClick={() => onAddToCollection?.(tag)}
                        >
                          {tag}
                        </button>
                      ))}
                      {!tagsExpanded && hiddenCount > 0 && (
                        <button type="button" className="tag-more" onClick={() => toggleTags(v.id)}>
                          +{hiddenCount} more
                        </button>
                      )}
                      {tagsExpanded && tags.length > 5 && (
                        <button type="button" className="tag-more" onClick={() => toggleTags(v.id)}>
                          show less
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
