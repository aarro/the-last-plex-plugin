export default function UnmatchedTags({ tags, onCreateCollection }) {
  const entries = Object.entries(tags ?? {});

  if (entries.length === 0) return null;

  return (
    <section>
      <h2>Frequent Unmatched Tags</h2>
      <p className="empty" style={{ marginBottom: 12 }}>
        Tags seen most often in unmatched videos — click to create a collection rule.
      </p>
      <div className="tags-grid">
        {entries.map(([tag, count]) => (
          <div className="tag-chip" key={tag}>
            <span>{tag}</span>
            <span className="count">{count}</span>
            <button
              title={`Create collection for "${tag}"`}
              onClick={() => onCreateCollection(tag)}
            >
              + create
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
