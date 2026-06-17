import { useState } from "react";

export default function ProjectPanel({ onCreateProject, loading }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");

    if (!name.trim()) {
      setError("نام پروژه الزامی است.");
      return;
    }

    try {
      await onCreateProject({
        name: name.trim(),
        description: description.trim(),
        metadata: {
          source: "frontend-project-panel",
        },
      });

      setName("");
      setDescription("");
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section className="panel-card">
      <div className="panel-card-header">
        <div>
          <h2>ایجاد پروژه</h2>
          <p>برای مدیریت حرفه‌ای uploadها، queryها و outputها یک پروژه بسازید.</p>
        </div>
      </div>

      <form className="form" onSubmit={handleSubmit}>
        <label>
          نام پروژه
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="مثلاً: Vegetation Monitoring - Region A"
          />
        </label>

        <label>
          توضیحات
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            placeholder="توضیح کوتاه درباره هدف پروژه"
          />
        </label>

        {error && <div className="alert error">{error}</div>}

        <button type="submit" disabled={loading}>
          {loading ? "در حال ساخت..." : "ایجاد پروژه"}
        </button>
      </form>
    </section>
  );
}
