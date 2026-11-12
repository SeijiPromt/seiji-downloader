const form = document.getElementById("form");
const urlInput = document.getElementById("url");
const go = document.getElementById("go");
const statusEl = document.getElementById("status");
const result = document.getElementById("result");
const thumb = document.getElementById("thumb");
const titleEl = document.getElementById("title");
const byline = document.getElementById("byline");
const formatsEl = document.getElementById("formats");

function setStatus(text, err = false) {
  statusEl.hidden = !text;
  statusEl.textContent = text || "";
  statusEl.classList.toggle("err", err);
}

function human(bytes) {
  if (!bytes) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

function fmtDuration(s) {
  if (!s) return "";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60).toString().padStart(2, "0");
  return `${m}:${sec}`;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;

  go.disabled = true;
  result.hidden = true;
  formatsEl.innerHTML = "";
  setStatus("resolving...");

  try {
    const res = await fetch("/api/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "resolve failed");

    titleEl.textContent = data.title;

    const bits = [data.uploader, data.extractor, fmtDuration(data.duration)]
      .filter(Boolean);
    byline.textContent = bits.join("  ·  ");

    if (data.thumbnail) {
      thumb.src = data.thumbnail;
      thumb.hidden = false;
    } else {
      thumb.hidden = true;
    }

    const items = [
      { id: "best", label: "best available", ext: "mp4", filesize: 0 },
      ...data.formats,
    ];

    for (const f of items) {
      const li = document.createElement("li");

      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = f.id === "best"
        ? "best available"
        : `${f.label} · ${f.ext}`;

      if (f.filesize) {
        const small = document.createElement("small");
        small.textContent = human(f.filesize);
        tag.appendChild(small);
      }

      const a = document.createElement("a");
      a.textContent = "download";
      a.href = `/api/download?url=${encodeURIComponent(url)}&format_id=${encodeURIComponent(f.id)}`;

      li.append(tag, a);
      formatsEl.appendChild(li);
    }

    result.hidden = false;
    setStatus("");
  } catch (err) {
    setStatus(err.message || "resolve failed", true);
  } finally {
    go.disabled = false;
  }
});
