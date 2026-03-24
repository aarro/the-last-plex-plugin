import { useEffect, useRef, useState } from "react";
import ReactCrop, { centerCrop, makeAspectCrop } from "react-image-crop";
import "react-image-crop/dist/ReactCrop.css";

const FIELDS = ["tags", "title", "channel", "uploader", "categories", "description", "extractor"];
const MATCHES = ["exact", "in"];

const IMAGE_TYPES = [
  { key: "image", label: "Poster", hint: "2:3 portrait (e.g. 680×1000)" },
  { key: "art", label: "Background", hint: "16:9 landscape (e.g. 1920×1080)" },
  { key: "logo", label: "Logo", hint: "PNG with transparency recommended" },
  { key: "square_art", label: "Square Art", hint: "1:1 square" },
];

// Locked aspect ratios for the crop tool (undefined = free crop for logo)
const CROP_ASPECTS = { image: 2 / 3, art: 16 / 9, logo: undefined, square_art: 1 };

/** Prevents click/key events from bubbling to the parent card toggle. */
const stopBubble = {
  onClick: (e) => e.stopPropagation(),
  onKeyDown: (e) => e.stopPropagation(),
};

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
      <div className="form-group">
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

function ThumbGrid({ videos, onVideoSearch }) {
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
          <button
            key={v.id}
            type="button"
            className="thumb-strip-item"
            title={v.title}
            onClick={() => onVideoSearch?.(v.title)}
          >
            {v.thumbnail ? (
              <img src={v.thumbnail} alt="" loading="lazy" />
            ) : (
              <div className="thumb-strip-placeholder" />
            )}
            <div className="thumb-title">{v.title}</div>
          </button>
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

/** A small thumbnail chip for one image type with a "Set" button below. */
function ImageChip({ imageType, url, onLightbox, onSet, hasSuggestions }) {
  const { key, label } = imageType;
  const setLabel = key === "square_art" && hasSuggestions ? "Set ✨" : "Set";

  return (
    <div className="image-chip" data-type={key}>
      {/* biome-ignore lint/a11y/useSemanticElements: dual-mode button (lightbox vs set) */}
      <div
        className="image-chip-thumb"
        role="button"
        tabIndex={0}
        title={url ? `View ${label}` : `Set ${label}`}
        onClick={() => (url ? onLightbox(url) : onSet())}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") url ? onLightbox(url) : onSet();
        }}
      >
        {url ? <img src={url} alt={label} /> : <span className="image-chip-label">{label}</span>}
      </div>
      <button type="button" className="btn-ghost btn-sm" onClick={onSet}>
        {setLabel}
      </button>
    </div>
  );
}

/** Lightbox overlay — click or Escape to dismiss. */
function Lightbox({ src, onClose }) {
  useEffect(() => {
    const handler = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    // biome-ignore lint/a11y/useSemanticElements: lightbox backdrop intentionally uses div with role
    <div
      className="lightbox-overlay"
      role="button"
      tabIndex={0}
      aria-label="Close"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClose();
      }}
    >
      <img className="lightbox-img" src={src} alt="" />
    </div>
  );
}

/** Modal for entering a URL for one image type. Square Art shows channel art suggestions. */
function UrlModal({ imageType, currentUrl, onSet, onClose, collectionName }) {
  const { key, label, hint } = imageType;
  const [draft, setDraft] = useState(currentUrl || "");
  const [options, setOptions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [cropMode, setCropMode] = useState(false);
  const [crop, setCrop] = useState(null);
  const [percentCrop, setPercentCrop] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const inputRef = useRef(null);
  const aspect = CROP_ASPECTS[key];

  // For Square Art, fetch suggestions when the modal opens
  useEffect(() => {
    if (key !== "square_art" || !collectionName) return;
    setLoading(true);
    fetch(`/api/channel-art?collection=${encodeURIComponent(collectionName)}`)
      .then((r) => r.json())
      .then((d) => setOptions(d.options ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [key, collectionName]);

  useEffect(() => {
    if (!cropMode) inputRef.current?.focus();
  }, [cropMode]);

  useEffect(() => {
    const handler = (e) => {
      if (e.key === "Escape") {
        if (cropMode) setCropMode(false);
        else onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, cropMode]);

  function onImageLoad(e) {
    const { width, height } = e.currentTarget;
    const initial =
      aspect != null
        ? centerCrop(makeAspectCrop({ unit: "%", width: 90 }, aspect, width, height), width, height)
        : { unit: "%", x: 5, y: 5, width: 90, height: 90 };
    setCrop(initial);
    setPercentCrop(initial);
  }

  async function saveToAssets(url) {
    setSaving(true);
    setSaveError(null);
    try {
      const resp = await fetch("/api/assets/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_url: url, collection: collectionName, type: key }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error ${resp.status}`);
      }
      const data = await resp.json();
      onSet(data.url);
      onClose();
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function cropAndSave() {
    if (!percentCrop) return;
    setSaving(true);
    setSaveError(null);
    try {
      const resp = await fetch("/api/assets/crop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_url: draft,
          x: percentCrop.x / 100,
          y: percentCrop.y / 100,
          w: percentCrop.width / 100,
          h: percentCrop.height / 100,
          collection: collectionName,
          type: key,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Server error ${resp.status}`);
      }
      const data = await resp.json();
      onSet(data.url);
      onClose();
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  }

  const hasValidUrl = isAbsoluteUrl(draft);

  return (
    // biome-ignore lint/a11y/useSemanticElements: modal backdrop uses div with role
    <div
      className="url-modal-overlay"
      role="button"
      tabIndex={-1}
      aria-label="Close"
      onClick={cropMode ? undefined : onClose}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !cropMode) onClose();
      }}
    >
      {/* biome-ignore lint/a11y/noStaticElementInteractions: stopPropagation on modal body */}
      <div
        className={`url-modal${cropMode ? " url-modal--crop" : ""}`}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <div className="url-modal-header">
          <strong>Set {label}</strong>
          <span className="url-modal-hint">{hint}</span>
        </div>

        {cropMode ? (
          <>
            <div className="crop-container">
              <ReactCrop
                crop={crop}
                onChange={(_, pct) => {
                  setCrop(pct);
                  setPercentCrop(pct);
                }}
                aspect={aspect}
                keepSelection
              >
                <img
                  src={draft}
                  alt=""
                  onLoad={onImageLoad}
                  onError={() => {
                    setCropMode(false);
                    setSaveError("Could not load image — try 'Set' instead of 'Crop & Set'.");
                  }}
                  className="crop-source-img"
                />
              </ReactCrop>
            </div>
            <div className="url-modal-actions">
              <button
                type="button"
                className="btn-primary btn-sm"
                disabled={saving || !percentCrop}
                onClick={cropAndSave}
              >
                {saving ? "Saving…" : "Apply Crop"}
              </button>
              <button type="button" className="btn-ghost btn-sm" disabled={saving} onClick={() => setCropMode(false)}>
                Back
              </button>
            </div>
            {saveError && <p className="url-modal-error">{saveError}</p>}
          </>
        ) : (
          <>
            {key === "square_art" && (
              <div className="suggestion-section">
                {loading && <p className="empty">Loading channel art…</p>}
                {!loading && options.length > 0 && (
                  <>
                    <p className="suggestion-label">Channel avatars from matched videos:</p>
                    <div className="suggestion-chips">
                      {options.map((opt) => (
                        <button
                          key={opt.uploader_url}
                          type="button"
                          className="suggestion-chip"
                          title={opt.channel}
                          onClick={() => setDraft(opt.avatar_url)}
                        >
                          {opt.avatar_url && <img src={opt.avatar_url} alt={opt.channel} />}
                          <span>{opt.channel}</span>
                        </button>
                      ))}
                    </div>
                  </>
                )}
                {!loading && options.length === 0 && (
                  <p className="empty">No channel art found yet — enter a URL manually.</p>
                )}
              </div>
            )}

            <input
              ref={inputRef}
              type="text"
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                setSaveError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && hasValidUrl) saveToAssets(draft);
              }}
              placeholder="https://…"
              className="url-modal-input"
            />
            {hasValidUrl && <img src={draft} alt="" className="url-modal-preview" />}
            <div className="url-modal-actions">
              <button
                type="button"
                className="btn-primary btn-sm"
                disabled={saving || !hasValidUrl}
                onClick={() => saveToAssets(draft)}
              >
                {saving ? "Saving…" : "Set"}
              </button>
              {hasValidUrl && (
                <button type="button" className="btn-ghost btn-sm" disabled={saving} onClick={() => setCropMode(true)}>
                  Crop & Set
                </button>
              )}
              <button type="button" className="btn-ghost btn-sm" disabled={saving} onClick={onClose}>
                Cancel
              </button>
              {draft && !saving && (
                <button type="button" className="btn-ghost btn-sm" onClick={() => setDraft("")}>
                  Clear
                </button>
              )}
            </div>
            {saveError && <p className="url-modal-error">{saveError}</p>}
          </>
        )}
      </div>
    </div>
  );
}

function CollectionCard({
  collection,
  videos,
  onChange,
  onDelete,
  otherNames,
  plexThumb,
  onVideoSearch,
  initialExpanded,
  onSave,
}) {
  const [expanded, setExpanded] = useState(!!initialExpanded);
  const [editName, setEditName] = useState(collection.name);
  const [nameError, setNameError] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [lightboxSrc, setLightboxSrc] = useState(null);
  const [urlModalKey, setUrlModalKey] = useState(null);
  const [channelArtOptions, setChannelArtOptions] = useState([]);

  const hasMatchedVideos = videos.some((v) => v.collections.includes(collection.name));

  // Pre-fetch Square Art suggestions when card is expanded and has matched videos.
  // Uses hasMatchedVideos (a stable bool) instead of the videos array reference to
  // avoid re-firing on every parent render.
  useEffect(() => {
    if (!expanded || !hasMatchedVideos) return;
    fetch(`/api/channel-art?collection=${encodeURIComponent(collection.name)}`)
      .then((r) => r.json())
      .then((d) => setChannelArtOptions(d.options ?? []))
      .catch(() => {});
  }, [expanded, collection.name, hasMatchedVideos]);

  useEffect(() => {
    setEditName(collection.name);
  }, [collection.name]);

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

  const toggleExpanded = () => {
    if (expanded) {
      setConfirmDelete(false);
      setLightboxSrc(null);
      setUrlModalKey(null);
    }
    setExpanded((v) => !v);
  };

  const posterSrc = (isAbsoluteUrl(collection.image) ? collection.image : null) || plexThumb;

  const openUrlModal = (key) => {
    setUrlModalKey(key);
  };

  const closeUrlModal = () => setUrlModalKey(null);

  const setImageUrl = (key, url) => {
    onChange({ ...collection, [key]: url });
  };

  const activeImageType = IMAGE_TYPES.find((t) => t.key === urlModalKey);

  return (
    <div className={`card${expanded ? " card--expanded" : ""}`} data-collection-name={collection.name}>
      {/* Left column: poster — click expands when collapsed, lightboxes when expanded */}
      {/* biome-ignore lint/a11y/useSemanticElements: dual-mode button */}
      <div
        className="card-poster-col"
        role="button"
        tabIndex={0}
        onClick={(e) => {
          e.stopPropagation();
          if (expanded) {
            if (posterSrc) setLightboxSrc(posterSrc);
          } else {
            toggleExpanded();
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.stopPropagation();
            if (expanded) {
              if (posterSrc) setLightboxSrc(posterSrc);
            } else {
              toggleExpanded();
            }
          }
        }}
        aria-label={expanded ? (posterSrc ? "View poster" : "Poster") : `Expand ${collection.name}`}
        title={expanded ? (posterSrc ? "View poster" : "Poster") : `Expand ${collection.name}`}
      >
        <div className="card-poster-inner">
          {posterSrc ? <img src={posterSrc} alt="" className="card-poster-img" /> : <div className="card-poster-ph" />}
        </div>
      </div>

      {/* Right column row 1: header */}
      {/* biome-ignore lint/a11y/useSemanticElements: contains nested interactive content */}
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

      {/* Right column row 2: body — always visible when expanded */}
      {expanded && (
        <>
          <div className="card-body-top" {...stopBubble}>
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

            {/* Image type chips */}
            <div className="image-chips-row">
              {IMAGE_TYPES.map((imageType) => (
                <ImageChip
                  key={imageType.key}
                  imageType={imageType}
                  url={isAbsoluteUrl(collection[imageType.key]) ? collection[imageType.key] : null}
                  onLightbox={setLightboxSrc}
                  onSet={() => openUrlModal(imageType.key)}
                  hasSuggestions={imageType.key === "square_art" && channelArtOptions.length > 0}
                />
              ))}
            </div>
          </div>

          {/* Full-width row 3: rules + video thumbs + footer */}
          <div className="card-body-bottom" {...stopBubble}>
            <p className="section-heading">Rules</p>
            {collection.rules.length === 0 && <p className="empty">No rules — add one below.</p>}
            <div className="rules">
              {collection.rules.map((rule, i) => (
                <RuleForm key={i} rule={rule} onChange={(r) => updateRule(i, r)} onRemove={() => removeRule(i)} />
              ))}
            </div>
            <button type="button" className="btn-ghost btn-sm add-rule-btn" onClick={addRule}>
              + Add Rule
            </button>

            {/* Matched video thumbnails */}
            <div className="card-thumb-section">
              <p className="section-heading" style={{ marginTop: 0 }}>
                Clips
              </p>
              <ThumbGrid videos={matched} onVideoSearch={onVideoSearch} />
            </div>

            {/* Footer: delete + save */}
            <div className="card-footer-row">
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
              {onSave && (
                <button type="button" className="btn-primary btn-sm" onClick={() => onSave(collection)}>
                  Save
                </button>
              )}
            </div>
          </div>
        </>
      )}

      {/* Lightbox */}
      {lightboxSrc && <Lightbox src={lightboxSrc} onClose={() => setLightboxSrc(null)} />}

      {/* URL modal */}
      {urlModalKey && activeImageType && (
        <UrlModal
          imageType={activeImageType}
          collectionName={collection.name}
          currentUrl={isAbsoluteUrl(collection[urlModalKey]) ? collection[urlModalKey] : ""}
          onSet={(url) => setImageUrl(urlModalKey, url)}
          onClose={closeUrlModal}
        />
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
          className={nameError ? "form-row-input input-error" : "form-row-input"}
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

export default function Collections({ collections, videos, onChange, onVideoSearch, onSave }) {
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState(null);
  const sectionRef = useRef(null);

  useEffect(() => {
    if (!newName || !sectionRef.current) return;
    const el = sectionRef.current.querySelector(`[data-collection-name="${CSS.escape(newName)}"]`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
    setNewName(null);
  }, [newName]);

  const updateAt = (i, c) => {
    const next = [...collections];
    next[i] = c;
    onChange(next);
  };

  const deleteAt = (i) => onChange(collections.filter((_, idx) => idx !== i));

  const addCollection = (c) => {
    onChange([...collections, c]);
    setAdding(false);
    setNewName(c.name);
  };

  const sorted = collections.map((c, i) => ({ c, i })).sort((a, b) => a.c.name.localeCompare(b.c.name));

  return (
    <section ref={sectionRef}>
      <h2>Collections</h2>
      <p className="collections-intro">
        Collections group videos from multiple channels, tags, or titles under one name in Plex — great when an artist,
        show, or topic spans different uploaders or video names. Add rules to define what matches; any video satisfying
        at least one rule joins the collection. Hit <strong>Save</strong> on a collection card to apply rules and push
        artwork to Plex.
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
          onVideoSearch={onVideoSearch}
          initialExpanded={c.name === newName}
          onSave={
            onSave
              ? (updatedCollection) => {
                  const next = [...collections];
                  next[i] = updatedCollection;
                  return onSave(next);
                }
              : undefined
          }
        />
      ))}

      {adding ? (
        <AddCollectionForm
          onAdd={addCollection}
          onCancel={() => setAdding(false)}
          existingNames={collections.map((c) => c.name)}
        />
      ) : (
        <button type="button" className="btn-ghost add-collection-btn" onClick={() => setAdding(true)}>
          + Add Collection
        </button>
      )}
    </section>
  );
}
