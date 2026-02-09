const TELE = document.getElementById("teletext");

const COLORS = {
  Y: "yellow", // default (no span needed)
  C: "cyan",
  G: "green",
  W: "white",
  R: "red",
  M: "magenta",
  B: "blue",
};

function padOrTrim(str, width) {
  const s = (str ?? "").toString();
  if (s.length === width) return s;
  if (s.length > width) return s.slice(0, width);
  return s + " ".repeat(width - s.length);
}

/**
 * Line markup:
 *  - Use {C} {G} {W} {R} {M} {B} at start of line to colour that entire line.
 *  - Example: "{C}101 NEWS HEADLINES"
 */
function renderLine(line) {
  if (!line) return "";
  const m = line.match(/^\{([YCGWRMB])\}(.*)$/);
  if (!m) return escapeHtml(line);

  const code = m[1];
  const rest = m[2] ?? "";
  if (code === "Y") return escapeHtml(rest);

  const cls = COLORS[code] || "white";
  return `<span class="${cls}">${escapeHtml(rest)}</span>`;
}

function escapeHtml(s){
  return (s ?? "").toString()
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;");
}

/**
 * Big block header font (very “Ceefax-y”)
 *  - Creates 5-row block letters using █ and spaces.
 */
const BIG = {
  "A": [" ██ ", "█  █", "████", "█  █", "█  █"],
  "B": ["███ ", "█  █", "███ ", "█  █", "███ "],
  "C": [" ███", "█   ", "█   ", "█   ", " ███"],
  "D": ["███ ", "█  █", "█  █", "█  █", "███ "],
  "E": ["████", "█   ", "███ ", "█   ", "████"],
  "F": ["████", "█   ", "███ ", "█   ", "█   "],
  "G": [" ███", "█   ", "█ ██", "█  █", " ███"],
  "H": ["█  █", "█  █", "████", "█  █", "█  █"],
  "I": ["████", " ██ ", " ██ ", " ██ ", "████"],
  "J": ["  ██", "   █", "   █", "█  █", " ██ "],
  "K": ["█  █", "█ █ ", "██  ", "█ █ ", "█  █"],
  "L": ["█   ", "█   ", "█   ", "█   ", "████"],
  "M": ["█  █", "████", "█ ██", "█  █", "█  █"],
  "N": ["█  █", "██ █", "█ ██", "█  █", "█  █"],
  "O": [" ██ ", "█  █", "█  █", "█  █", " ██ "],
  "P": ["███ ", "█  █", "███ ", "█   ", "█   "],
  "Q": [" ██ ", "█  █", "█  █", "█ ██", " ███"],
  "R": ["███ ", "█  █", "███ ", "█ █ ", "█  █"],
  "S": [" ███", "█   ", " ██ ", "   █", "███ "],
  "T": ["████", " ██ ", " ██ ", " ██ ", " ██ "],
  "U": ["█  █", "█  █", "█  █", "█  █", " ██ "],
  "V": ["█  █", "█  █", "█  █", " █ █", "  █ "],
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

  // Center each row into the teletext width
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

  // Optional top status line (like “P100 … time”)
  const status = page.statusLine || "";
  if (status) out.push(padOrTrim(status, width));
  else out.push(padOrTrim("", width));

  // Optional big header (5 rows)
if (page.bigHeader) {
  const headerLines = makeBigHeader(page.bigHeader, width).map(l => padOrTrim(l, width));
  out.push(...headerLines);
} else {
  out.push(...Array(5).fill(padOrTrim("", width)));
}

  // Body lines
  const body = (page.lines || []).slice(0, height - out.length);
  while (body.length < (height - out.length)) body.push("");

  for (const line of body) out.push(padOrTrim(line, width));

  // Ensure exactly 24 rows
  return out.slice(0, height);
}

async function fetchJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to fetch ${path} (${res.status})`);
  return res.json();
}

let playlist = [];
let secondsPerPage = 10;
let idx = 0;

async function loadChannel() {
  const chan = await fetchJson("channel.json");
  secondsPerPage = Number(chan.secondsPerPage) || 10;
  playlist = chan.pages || [];
}

async function showNext() {
  if (!playlist.length) return;

  const path = playlist[idx % playlist.length];
  idx++;

  try {
    const page = await fetchJson(path);
    const lines = buildScreen(page);

    // Render with per-line colour support
    const html = lines.map(renderLine).join("\n");
    TELE.innerHTML = html;
  } catch (e) {
    TELE.textContent = `Error loading page:\n${path}\n\n${e.message}`;
  }
}

(async function start() {
  await loadChannel();
  await showNext();
  setInterval(showNext, secondsPerPage * 1000);
})();
