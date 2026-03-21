import { useState } from "react";

const FIELDS = ["tags", "title", "channel", "uploader", "categories", "description", "extractor"];
const MATCHES = ["exact", "in"];

function emptyRule() {
  return { field: "tags", values: [], match: "exact" };
}

function RuleForm({ rule, onChange, onRemove }) {
  const [rawValues, setRawValues] = useState(rule.values.join(", "));

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
          value={rawValues}
          onChange={(e) => {
            setRawValues(e.target.value);
            onChange({
              ...rule,
              values: e.target.value.split(",").map((v) => v.trim()).filter(Boolean),
            });
          }}
          placeholder="value one, value two"
        />
      </div>
      <button className="btn-danger btn-sm" onClick={onRemove} title="Remove rule">✕</button>
    </div>
  );
}

const THUMB_PAGE = 5;

function ThumbGrid({ videos }) {
  const [expanded, setExpanded] = useState(false);
  if (videos.length === 0) {
    return <p className="empty" style={{ paddingTop: 8 }}>No videos matched yet.</p>;
  }
  const shown = expanded ? videos : videos.slice(0, THUMB_PAGE);
  const hidden = videos.length - THUMB_PAGE;
  return (
    <>
      <div className="thumb-grid">
        {shown.map((v) => (
          <div key={v.id} className="thumb-strip-item" title={v.title}>
            {v.thumbnail
              ? <img src={v.thumbnail} alt="" loading="lazy" />
              : <div className="thumb-strip-placeholder" />
            }
            <div className="thumb-title">{v.title}</div>
          </div>
        ))}
      </div>
      {videos.length > THUMB_PAGE && (
        <button className="btn-ghost btn-sm" style={{ marginTop: 6 }}
          onClick={() => setExpanded(v => !v)}>
          {expanded ? "Show fewer ▲" : `Show ${hidden} more ▼`}
        </button>
      )}
    </>
  );
}

function isAbsoluteUrl(url) {
  return typeof url === "string" && (url.startsWith("http://") || url.startsWith("https://"));
}

function CollectionCard({ collection, videos, onChange, onDelete, otherNames, plexThumb }) {
  const [expanded, setExpanded] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [editName, setEditName] = useState(collection.name);
  const [editing, setEditing] = useState(false);
  const [nameError, setNameError] = useState(null);
  const [imageEditing, setImageEditing] = useState(false);
  const savedImage = isAbsoluteUrl(collection.image) ? collection.image : null;
  const [editImageUrl, setEditImageUrl] = useState(savedImage || "");

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
    const trimmed = editName.trim();
    if (!trimmed) { setEditing(false); return; }
    if (otherNames.includes(trimmed)) {
      setNameError(`"${trimmed}" already exists`);
      return;
    }
    setNameError(null);
    onChange({ ...collection, name: trimmed });
    setEditing(false);
  };

  const saveImage = () => {
    const url = editImageUrl.trim();
    onChange({ ...collection, image: isAbsoluteUrl(url) ? url : null });
    setImageEditing(false);
  };

  const clearImage = () => {
    setEditImageUrl("");
    onChange({ ...collection, image: null });
    setImageEditing(false);
  };

  return (
    <div className="card">
      <div className="card-header" onClick={() => setExpanded((v) => !v)}>
        {(savedImage || plexThumb) && (
          <img src={savedImage || plexThumb} alt="" style={{ width: 40, height: 40, objectFit: "cover", borderRadius: 4, flexShrink: 0 }} />
        )}
        {editing ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }} onClick={(e) => e.stopPropagation()}>
            <input
              type="text"
              className="card-title"
              value={editName}
              onChange={(e) => { setEditName(e.target.value); setNameError(null); }}
              onBlur={saveName}
              onKeyDown={(e) => { if (e.key === "Enter") saveName(); if (e.key === "Escape") { setEditing(false); setNameError(null); setEditName(collection.name); } }}
              autoFocus
              style={{ background: "none", border: "none", borderBottom: `1px solid ${nameError ? "var(--danger, #e55)" : "var(--accent)"}`, borderRadius: 0, padding: "0 2px", width: "auto", fontSize: 15 }}
            />
            {nameError && <span style={{ fontSize: 11, color: "var(--danger, #e55)" }}>{nameError}</span>}
          </div>
        ) : (
          <span className="card-title">{collection.name}</span>
        )}
        <div className="card-actions" onClick={(e) => e.stopPropagation()}>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            {matched.length} video{matched.length !== 1 ? "s" : ""}
          </span>
          <button className="btn-icon" title="Rename" onClick={() => { setEditing(true); setExpanded(true); }}>✏️</button>
          {matched.length > 0 && (
            <button className="btn-icon" title="Set collection image" onClick={() => {
            setImageEditing(v => !v);
            setExpanded(true);
          }}>📷</button>
          )}
          <button className="btn-icon btn-danger" title="Delete collection" onClick={onDelete}>🗑</button>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>{expanded ? "▲" : "▼"}</span>
        </div>
      </div>

      {expanded && (
        <div className="card-body">
          {imageEditing && (
            <div className="image-edit-row" onClick={(e) => e.stopPropagation()}>
              <input
                type="text"
                value={editImageUrl}
                onChange={(e) => setEditImageUrl(e.target.value)}
                placeholder="https://…"
                style={{ flex: 1 }}
              />
              {(editImageUrl || plexThumb) && (
                <img src={editImageUrl || plexThumb} alt="" style={{ width: 40, height: 40, objectFit: "cover", borderRadius: 4, flexShrink: 0 }} />
              )}
              <button className="btn-primary btn-sm" onClick={saveImage}>Save</button>
              <button className="btn-ghost btn-sm" onClick={clearImage}>Clear</button>
            </div>
          )}
          <ThumbGrid videos={matched} />

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

function AddCollectionForm({ onAdd, onCancel, existingNames }) {
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState(null);

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (existingNames.includes(trimmed)) {
      setNameError(`"${trimmed}" already exists`);
      return;
    }
    onAdd({ name: trimmed, rules: [emptyRule()] });
  };

  return (
    <div className="add-collection-form">
      <div className="form-row">
        <div className="form-group">
          <label>New collection name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => { setName(e.target.value); setNameError(null); }}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); if (e.key === "Escape") onCancel(); }}
            placeholder="e.g. GoGo Penguin"
            autoFocus
            style={nameError ? { borderColor: "var(--danger, #e55)" } : {}}
          />
          {nameError && <span style={{ fontSize: 11, color: "var(--danger, #e55)" }}>{nameError}</span>}
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

  const sorted = collections.map((c, i) => ({ c, i })).sort((a, b) => a.c.name.localeCompare(b.c.name));

  return (
    <section>
      <h2>Collections</h2>
      <p className="collections-intro">
        Collections group videos from multiple channels, tags, or titles under one name in
        Plex — great when an artist, show, or topic spans different uploaders or video names.
        Add rules to define what matches; any video satisfying at least one rule joins the
        collection. Hit <strong>Save Changes</strong> to apply rules and push artwork to Plex.
      </p>

      {adding && (
        <AddCollectionForm onAdd={addCollection} onCancel={() => setAdding(false)} existingNames={collections.map((c) => c.name)} />
      )}

      {collections.length === 0 && !adding && (
        <p className="empty">No collections yet. Add one to get started.</p>
      )}

      {sorted.map(({ c, i }) => (
        <CollectionCard
          key={c.name}
          collection={c}
          videos={videos}
          onChange={(updated) => updateAt(i, updated)}
          onDelete={() => deleteAt(i)}
          otherNames={collections.filter((_, idx) => idx !== i).map((x) => x.name)}
          plexThumb={c.plex_thumb}
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
