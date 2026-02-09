// Tiny standalone preview builder (shares the same “buildScreen” logic as app.js, duplicated for simplicity)

const $ = (id) => document.getElementById(id);
const preview = $("preview");

function padOrTrim(str, width) {
  const s = (str ?? "").toString();
  if (s.length === width) return s;
  if (s.length > width) return s.slice(0, width);
  return s + " ".repeat(width - s.length);
}

function escapeHtml(s){
  return (s ?? "").toString()
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;");
}

const COLORS = { C:"cyan", G:"green", W:"white", R:"red", M:"magenta", B:"blue", Y:"yellow" };

function renderLine(line) {
  if (!line) return "";
  const m = line.match(/^\{([YCGWRMB])\}(.*)$/);
  if (!m) return escapeHtml(line);
  const code = m[1];
  const rest = m[2] ?? "";
  if (code === "Y") return escapeHtml(rest);
  return `<span class="${COLORS[code] || "white"}">${escapeHtml(rest)}</span>`;
}

// same big font as app.js (kept short-ish; you can copy/paste the BIG map from app.js)
const BIG = {
  "A": [" ██ ", "█  █", "████", "█  █", "█  █"],
  "B": ["███ ", "█  █", "███ ", "█  █", "███ "],
  "C": [" ███", "█   ", "█   ", "█   ", " ███"],
  "E": ["████", "█   ", "███ ", "█   ", "████"],
  "F": ["████", "█   ", "███ ", "█   ", "█   "],
  "H": ["█  █", "█  █", "████", "█  █", "█  █"],
  "N": ["█  █", "██ █", "█ ██", "█  █", "█  █"],
  "O": [" ██ ", "█  █", "█  █", "█  █", " ██ "],
  "S": [" ███", "█   ", " ██ ", "   █", "███ "],
  "T": ["████", " ██ ", " ██ ", " ██ ", " ██ "],
  "W": ["█  █", "█  █", "█ ██", "████", "█  █"],
  "X": ["█  █", " █ █", "  █ ", " █ █", "█  █"],
  "Y": ["█  █", " █ █", "  █ ", "  █ ", "  █ "],
  "Z": ["████", "  █ ", " ██ ", "█   ", "████"],
  "0": [" ██ ", "█  █", "█ ██", "█  █", " ██ "],
  "1": [" ██ ", "██  ", " ██ ", " ██ ", "████"],
  "2": ["███ ", "   █", " ██ ", "█   ", "████"],
  "3": ["███ ", "   █", " ██ ", "   █", "███ "],
  "4": ["█  █", "█  █", "████", "   █", "   █"],
  "5": ["████", "█   ", "███ ", "   █", "███ "],
  "6": [" ██ ", "█   ", "███ ", "█  █", " ██ "],
  "7": ["████", "   █", "  █ ", " █  ", "█   "],
  "8": [" ██ ", "█  █", " ██ ", "█  █", " ██ "],
  "9": [" ██ ", "█  █", " ███", "   █", " ██ "],
  " ": ["  ", "  ", "  ", "  ", "  "],
  "-": ["    ", "    ", "████", "    ", "    "]
};

function makeBigHeader(text, width = 40) {
  const t = (text ?? "").toUpperCase().slice(0, 16);
  const rows = ["", "", "", "", ""];
  for (const ch of t) {
    const glyph = BIG[ch] || BIG[" "];
    for (let i = 0; i < 5; i++) rows[i] += glyph[i] + " ";
  }
  return rows.map(r => {
    const raw = r.replace(/\s+$/,"");
    const pad = Math.max(0, Math.floor((width - raw.length) / 2));
    return " ".repeat(pad) + raw;
  });
}

function buildScreen(page) {
  const width = 40;
  const height = 24;
  const out = [];

  const status = page.statusLine || "";
  out.push(padOrTrim(status, width));

if (page.bigHeader) {
  const headerLines = makeBigHeader(page.bigHeader, width).map(l => padOrTrim(l, width));
  out.push(...headerLines);
} else {
  out.push(...Array(5).fill(padOrTrim("", width)));
}


  const body = (page.lines || []).slice(0, height - out.length);
  while (body.length < (height - out.length)) body.push("");
  for (const line of body) out.push(padOrTrim(line, width));

  return out.slice(0, height);
}

function currentPageJson() {
  const lines = $("lines").value.split("\n");
  return {
    statusLine: $("statusLine").value,
    bigHeader: $("bigHeader").value,
    lines
  };
}

function renderPreview() {
  const page = currentPageJson();
  const lines = buildScreen(page);
  preview.innerHTML = lines.map(renderLine).join("\n");
}

function download(filename, text) {
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

$("statusLine").addEventListener("input", renderPreview);
$("bigHeader").addEventListener("input", renderPreview);
$("lines").addEventListener("input", renderPreview);

$("download").addEventListener("click", () => {
  const page = currentPageJson();
  const pretty = JSON.stringify(page, null, 2);
  download("page.json", pretty);
});

$("copy").addEventListener("click", async () => {
  const pretty = JSON.stringify(currentPageJson(), null, 2);
  await navigator.clipboard.writeText(pretty);
});

$("lines").value = [
  "",
  "Type your page here.",
  "{C}Use {C} {G} {W} etc at line start.",
  "",
  "Drop the JSON into /pages and add it",
  "to channel.json to include in rotation."
].join("\n");

renderPreview();
