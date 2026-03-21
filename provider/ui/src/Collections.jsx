import { useState } from "react";

const FIELDS = ["tags", "title", "channel", "uploader", "categories", "description", "extractor"];
const MATCHES = ["exact", "in"];

function emptyRule() {
  return { field: "tags", values: [], match: "exact" };
}

function RuleForm({ rule, onChange, onRemove }) {
  return (
    <div className="rule-form">
      <div className="form-group">
        <label>Field</label>
        <select value={rule.field} onChange={(e) => onChange({ ...rule, field: e.target.value })}>
          {FIELDS.map((f) => <option key={f}>{f}</option>)}
        </select>
      </div>
      <div className="form-group">
        <label>Match</label>
        <select value={rule.match} onChange={(e) => onChange({ ...rule, match: e.target.value })}>
          {MATCHES.map((m) => <option key={m}>{m}</option>)}
        </select>
      </div>
      <div className="form-group" style={{ flex: 2 }}>
        <label>Values (comma-separated)</label>
        <input
          type="text"
          value={rule.values.join(", ")}
          onChange={(e) =>
            onChange({
              ...rule,
              values: e.target.value.split(",").map((v) => v.trim()).filter(Boolean),
            })
          }
          placeholder="value one, value two"
        />
      </div>
      <button className="btn-danger btn-sm" onClick={onRemove} title="Remove rule">✕</button>
    </div>
  );
}

function ThumbStrip({ videos }) {
  if (videos.length === 0) {
    return <p className="empty" style={{ paddingTop: 8 }}>No videos matched yet.</p>;
  }
  return (
    <div className="thumb-strip">
      {videos.map((v) => (
        <div key={v.id} className="thumb-strip-item" title={v.title}>
          {v.thumbnail
            ? <img src={v.thumbnail} alt="" loading="lazy" />
            : <div className="thumb-strip-placeholder" />
          }
          <div className="thumb-title">{v.title}</div>
        </div>
      ))}
    </div>
  );
}

function CollectionCard({ collection, videos, onChange, onDelete }) {
  const [expanded, setExpanded] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [editName, setEditName] = useState(collection.name);
  const [editing, setEditing] = useState(false);

  const matched = videos.filter((v) => v.collections.includes(collection.name));

  const updateRule = (i, rule) => {
    const rules = [...collection.rules];
    rules[i] = rule;
    onChange({ ...collection, rules });
  };

  const removeRule = (i) => {
    onChange({ ...collection, rules: collection.rules.filter((_, idx) => idx !== i) });
  };

  const addRule = () => {
    onChange({ ...collection, rules: [...collection.rules, emptyRule()] });
  };

  const saveName = () => {
    if (editName.trim()) onChange({ ...collection, name: editName.trim() });
    setEditing(false);
  };

  return (
    <div className="card">
      <div className="card-header" onClick={() => setExpanded((v) => !v)}>
        {editing ? (
          <input
            type="text"
            className="card-title"
            value={editName}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setEditName(e.target.value)}
            onBlur={saveName}
            onKeyDown={(e) => { if (e.key === "Enter") saveName(); }}
            autoFocus
            style={{ background: "none", border: "none", borderBottom: "1px solid var(--accent)", borderRadius: 0, padding: "0 2px", width: "auto", fontSize: 15 }}
          />
        ) : (
          <span className="card-title">{collection.name}</span>
        )}
        <div className="card-actions" onClick={(e) => e.stopPropagation()}>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            {matched.length} video{matched.length !== 1 ? "s" : ""}
          </span>
          <button className="btn-icon" title="Rename" onClick={() => { setEditing(true); setExpanded(true); }}>✏️</button>
          <button className="btn-icon btn-danger" title="Delete collection" onClick={onDelete}>🗑</button>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>{expanded ? "▲" : "▼"}</span>
        </div>
      </div>

      {expanded && (
        <div className="card-body">
          <ThumbStrip videos={matched} />

          <div className="rules-toggle" onClick={() => setRulesOpen((v) => !v)}>
            <span className="rules-toggle-arrow">{rulesOpen ? "▲" : "▶"}</span>
            <span>Rules ({collection.rules.length})</span>
          </div>

          {rulesOpen && (
            <>
              {collection.rules.length === 0 && (
                <p className="empty">No rules — add one below.</p>
              )}
              <div className="rules">
                {collection.rules.map((rule, i) => (
                  <RuleForm
                    key={i}
                    rule={rule}
                    onChange={(r) => updateRule(i, r)}
                    onRemove={() => removeRule(i)}
                  />
                ))}
              </div>
              <button className="btn-ghost btn-sm" style={{ marginTop: 10 }} onClick={addRule}>
                + Add Rule
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function AddCollectionForm({ onAdd, onCancel }) {
  const [name, setName] = useState("");

  const submit = () => {
    if (name.trim()) {
      onAdd({ name: name.trim(), rules: [emptyRule()] });
    }
  };

  return (
    <div className="add-collection-form">
      <div className="form-row">
        <div className="form-group">
          <label>New collection name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); if (e.key === "Escape") onCancel(); }}
            placeholder="e.g. GoGo Penguin"
            autoFocus
          />
        </div>
        <button className="btn-primary" onClick={submit} disabled={!name.trim()}>Add</button>
        <button className="btn-ghost" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function Collections({ collections, videos, onChange }) {
  const [adding, setAdding] = useState(false);

  const updateAt = (i, c) => {
    const next = [...collections];
    next[i] = c;
    onChange(next);
  };

  const deleteAt = (i) => onChange(collections.filter((_, idx) => idx !== i));

  const addCollection = (c) => {
    onChange([...collections, c]);
    setAdding(false);
  };

  return (
    <section>
      <h2>Collections</h2>

      {adding && (
        <AddCollectionForm onAdd={addCollection} onCancel={() => setAdding(false)} />
      )}

      {collections.length === 0 && !adding && (
        <p className="empty">No collections yet. Add one to get started.</p>
      )}

      {collections.map((c, i) => (
        <CollectionCard
          key={i}
          collection={c}
          videos={videos}
          onChange={(updated) => updateAt(i, updated)}
          onDelete={() => deleteAt(i)}
        />
      ))}

      {!adding && (
        <button className="btn-ghost" style={{ marginTop: 4 }} onClick={() => setAdding(true)}>
          + Add Collection
        </button>
      )}
    </section>
  );
}
