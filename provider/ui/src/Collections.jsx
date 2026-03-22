import { useEffect, useState } from "react";

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
        <label>
          Field
          <select value={rule.field} onChange={(e) => onChange({ ...rule, field: e.target.value })}>
            {FIELDS.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="form-group">
        <label>
          Match
          <select value={rule.match} onChange={(e) => onChange({ ...rule, match: e.target.value })}>
            {MATCHES.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="form-group" style={{ flex: 2 }}>
        <label>
          Values (comma-separated)
          <input
            type="text"
            value={rawValues}
            onChange={(e) => {
              setRawValues(e.target.value);
              onChange({
                ...rule,
                values: e.target.value
                  .split(",")
                  .map((v) => v.trim())
                  .filter(Boolean),
              });
            }}
            placeholder="value one, value two"
          />
        </label>
      </div>
      <button type="button" className="btn-danger btn-sm" onClick={onRemove} title="Remove rule">
        ✕
      </button>
    </div>
  );
}

const THUMB_PAGE = 4;

function ThumbGrid({ videos }) {
  const [expanded, setExpanded] = useState(false);
  if (videos.length === 0) {
    return <p className="empty thumb-grid-empty">No videos matched yet.</p>;
  }
  const shown = expanded ? videos : videos.slice(0, THUMB_PAGE);
  const hidden = videos.length - THUMB_PAGE;
  return (
    <>
      <div className="thumb-grid">
        {shown.map((v) => (
          <div key={v.id} className="thumb-strip-item" title={v.title}>
            {v.thumbnail ? (
              <img src={v.thumbnail} alt="" loading="lazy" />
            ) : (
              <div className="thumb-strip-placeholder" />
            )}
            <div className="thumb-title">{v.title}</div>
          </div>
        ))}
      </div>
      {videos.length > THUMB_PAGE && (
        <button type="button" className="btn-ghost btn-sm thumb-grid-toggle" onClick={() => setExpanded((v) => !v)}>
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
  const [editName, setEditName] = useState(collection.name);
  const [nameError, setNameError] = useState(null);
  const [imageEditing, setImageEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const savedImage = isAbsoluteUrl(collection.image) ? collection.image : null;
  const [editImageUrl, setEditImageUrl] = useState(savedImage || "");
  const posterSrc = savedImage || plexThumb;

  useEffect(() => {
    setEditName(collection.name);
  }, [collection.name]);

  useEffect(() => {
    setEditImageUrl(isAbsoluteUrl(collection.image) ? collection.image : "");
  }, [collection.image]);

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
    if (!trimmed) {
      setEditName(collection.name);
      return;
    }
    if (otherNames.includes(trimmed)) {
      setNameError(`"${trimmed}" already exists`);
      return;
    }
    setNameError(null);
    onChange({ ...collection, name: trimmed });
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

  const toggleExpanded = () => {
    if (expanded) {
      setImageEditing(false);
      setConfirmDelete(false);
    }
    setExpanded((v) => !v);
  };

  const handlePosterClick = (e) => {
    e.stopPropagation();
    if (expanded) {
      setImageEditing((v) => !v);
    } else {
      toggleExpanded();
    }
  };

  const handlePosterKey = (e) => {
    if (e.key === "Enter" || e.key === " ") handlePosterClick(e);
  };

  return (
    <div className={`card${expanded ? " card--expanded" : ""}`}>
      {/* Left column: poster/thumbnail — toggles expand when collapsed, image editor when expanded */}
      {/* biome-ignore lint/a11y/useSemanticElements: poster is interactive in two different modes depending on expanded state */}
      <div
        className="card-poster-col"
        role="button"
        tabIndex={0}
        onClick={handlePosterClick}
        onKeyDown={handlePosterKey}
        aria-label={expanded ? "Change collection image" : `Expand ${collection.name}`}
        title={expanded ? "Change collection image" : `Expand ${collection.name}`}
      >
        <div className="card-poster-inner">
          {posterSrc ? <img src={posterSrc} alt="" className="card-poster-img" /> : <div className="card-poster-ph" />}
          {expanded && <div className="card-poster-overlay">Change Image</div>}
        </div>
      </div>

      {/* Right column row 1: header — always visible, toggles expand */}
      {/* biome-ignore lint/a11y/useSemanticElements: contains nested interactive content in body */}
      <div
        className="card-header-right"
        role="button"
        tabIndex={0}
        onClick={toggleExpanded}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") toggleExpanded();
        }}
      >
        <span className="card-title">{collection.name}</span>
        <span className="card-count">
          {matched.length} video{matched.length !== 1 ? "s" : ""}
          <span>{expanded ? " ▲" : " ▼"}</span>
        </span>
      </div>

      {/* Right column row 2: body — only when expanded */}
      {expanded && (
        // biome-ignore lint/a11y/noStaticElementInteractions: stops propagation to parent role=button
        <div className="card-body" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          {/* Image URL editor — shown when poster is clicked */}
          {imageEditing && (
            <div className="image-edit-row">
              <input
                type="text"
                value={editImageUrl}
                onChange={(e) => setEditImageUrl(e.target.value)}
                placeholder="https://…"
                style={{ flex: 1 }}
              />
              {(editImageUrl || plexThumb) && (
                <img src={editImageUrl || plexThumb} alt="" className="image-edit-preview" />
              )}
              <button type="button" className="btn-primary btn-sm" onClick={saveImage}>
                Save
              </button>
              <button type="button" className="btn-ghost btn-sm" onClick={clearImage}>
                Clear
              </button>
            </div>
          )}

          {/* Name editing */}
          <div className="form-group">
            <label>
              Name
              <input
                type="text"
                value={editName}
                onChange={(e) => {
                  setEditName(e.target.value);
                  setNameError(null);
                }}
                onBlur={saveName}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveName();
                }}
              />
            </label>
            {nameError && <span className="field-error">{nameError}</span>}
          </div>

          {/* Matched video thumbnails */}
          <ThumbGrid videos={matched} />

          {/* Rules */}
          {collection.rules.length === 0 && <p className="empty">No rules — add one below.</p>}
          <div className="rules">
            {collection.rules.map((rule, i) => (
              <RuleForm key={i} rule={rule} onChange={(r) => updateRule(i, r)} onRemove={() => removeRule(i)} />
            ))}
          </div>
          <button
            type="button"
            className="btn-ghost btn-sm"
            style={{ marginTop: 10, display: "block", marginLeft: "auto" }}
            onClick={addRule}
          >
            + Add Rule
          </button>

          {/* Delete with two-step confirmation */}
          <div className="card-delete-row">
            {confirmDelete ? (
              <>
                <button type="button" className="btn-danger btn-sm" onClick={onDelete}>
                  Confirm delete
                </button>
                <button type="button" className="btn-ghost btn-sm" onClick={() => setConfirmDelete(false)}>
                  Cancel
                </button>
              </>
            ) : (
              <button type="button" className="btn-danger btn-sm" onClick={() => setConfirmDelete(true)}>
                ✕ Delete collection
              </button>
            )}
          </div>
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
        <input
          type="text"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setNameError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
            if (e.key === "Escape") onCancel();
          }}
          placeholder="New collection name, e.g. GoGo Penguin"
          autoFocus
          className={nameError ? "input-error" : undefined}
          style={{ flex: 1 }}
        />
        <button type="button" className="btn-primary" onClick={submit} disabled={!name.trim()}>
          Add
        </button>
        <button type="button" className="btn-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {nameError && <span className="field-error">{nameError}</span>}
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
        Collections group videos from multiple channels, tags, or titles under one name in Plex — great when an artist,
        show, or topic spans different uploaders or video names. Add rules to define what matches; any video satisfying
        at least one rule joins the collection. Hit <strong>Save Changes</strong> to apply rules and push artwork to
        Plex.
      </p>

      {collections.length === 0 && !adding && <p className="empty">No collections yet. Add one to get started.</p>}

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

      {adding ? (
        <AddCollectionForm
          onAdd={addCollection}
          onCancel={() => setAdding(false)}
          existingNames={collections.map((c) => c.name)}
        />
      ) : (
        <button type="button" className="btn-ghost" style={{ marginTop: 4 }} onClick={() => setAdding(true)}>
          + Add Collection
        </button>
      )}
    </section>
  );
}
