"""Exploration du site : enregistre la STRUCTURE des pages et des échanges, jamais les valeurs sensibles.

Ce qui est conservé : chemins (identifiants remplacés), noms de paramètres, noms d'en-têtes, formes des
réponses JSON (types, et seulement de courts libellés alphabétiques), squelette de la page (balises,
attributs, textes courts), noms de cookies et de clés de stockage.

Ce qui n'est jamais conservé : valeurs de cookies et de stockage, valeurs d'en-têtes (Authorization…),
valeurs de paramètres d'URL, mots de passe et autres saisies, e-mails, jetons, identifiants.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Courts libellés (valeurs d'énumération comme « open » ou « Asterion ») : lettres, espaces, tirets.
LABEL_RE = re.compile(r"^[A-Za-zÀ-ÿ_\- ]{1,20}$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Services tiers sans intérêt pour comprendre le jeu.
SKIP_HOST_PARTS = (
    "google", "gstatic", "doubleclick", "facebook", "cloudflareinsights", "challenges.cloudflare",
    "sentry", "googletagmanager", "openai.com",
)
MAX_REQUESTS = 400
MAX_SNAPSHOTS = 500
MAX_SHAPES_PER_DIRECTION = 60
MAX_DEPTH = 6

# Squelette de la page : exécuté dans le navigateur. Ne lit JAMAIS la valeur d'un champ.
SNAPSHOT_JS = """
() => {
  const out = [];
  const interesting = 'a,button,input,select,textarea,form,label,h1,h2,h3,h4,nav,header,main,footer,'
    + 'section,article,aside,dialog,[role],[data-testid],[aria-label],img[alt]';
  const cssPath = (el) => {
    const parts = [];
    for (let n = el, i = 0; n && n.nodeType === 1 && i < 4; n = n.parentElement, i++) {
      let p = n.tagName.toLowerCase();
      if (n.id) p += '#' + n.id;
      else if (n.classList.length) p += '.' + [...n.classList].slice(0, 3).join('.');
      parts.unshift(p);
    }
    return parts.join(' > ');
  };
  for (const el of document.querySelectorAll(interesting)) {
    if (out.length >= 1200) break;
    const r = el.getBoundingClientRect();
    const o = { tag: el.tagName.toLowerCase(), path: cssPath(el), visible: r.width > 0 && r.height > 0 };
    if (el.id) o.id = el.id;
    for (const a of ['role', 'aria-label', 'name', 'type', 'placeholder', 'href', 'data-testid', 'alt', 'disabled']) {
      const v = el.getAttribute(a);
      if (v !== null) o[a] = a === 'href' ? v.split('?')[0].split('#')[0] : v.slice(0, 80);
    }
    const own = [...el.childNodes].filter((c) => c.nodeType === 3).map((c) => c.textContent).join(' ');
    let txt = own.trim();
    if (!txt && el.matches('button,a,label,h1,h2,h3,h4')) txt = el.innerText || '';
    txt = txt.trim().replace(/\\s+/g, ' ').slice(0, 80);
    if (txt) o.text = txt;
    o.box = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
    out.push(o);
  }
  // Textes visibles (niveaux, coûts, durées…) : éléments « feuilles » uniquement, sans valeur de champ.
  const texts = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (texts.length >= 800) break;
    if (el.children.length || el.matches('script,style,noscript,input,textarea,select,option')) continue;
    const t = (el.textContent || '').trim().replace(/\\s+/g, ' ');
    if (!t) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    texts.push({ path: cssPath(el), text: t.slice(0, 80) });
  }
  return { title: document.title, viewport: [innerWidth, innerHeight], elements: out, texts };
}
"""


def mask_text(text: str, limit: int = 80) -> str:
    return EMAIL_RE.sub("<email>", " ".join(str(text).split()))[:limit]


def _looks_like_id(segment: str) -> bool:
    if UUID_RE.match(segment):
        return True
    if segment.isdigit() and len(segment) >= 4:
        return True
    if len(segment) >= 12 and re.search(r"\d", segment):
        return True
    return len(segment) >= 24


UUID_ANYWHERE_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def mask_key(name: str) -> str:
    """Nom d'une clé de stockage ou d'un cookie : les identifiants qu'il contient sont remplacés par {id}."""
    return UUID_ANYWHERE_RE.sub("{id}", str(name))


def mask_path(path: str) -> str:
    return "/".join("{id}" if _looks_like_id(seg) else seg for seg in path.split("/"))


def mask_url(url: str) -> str:
    """hôte + chemin (identifiants remplacés) + NOMS des paramètres ; jamais leurs valeurs."""
    parts = urlsplit(url)
    names = sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)})
    query = ("?" + "&".join(names)) if names else ""
    return f"{parts.netloc}{mask_path(parts.path)}{query}"


def shape(value: Any, depth: int = 0) -> Any:
    """Forme d'une valeur JSON : les clés et les types, pas le contenu."""
    if depth >= MAX_DEPTH:
        return "<…>"
    if isinstance(value, dict):
        return {
            ("<id>" if _looks_like_id(str(k)) else str(k)): shape(v, depth + 1)
            for k, v in list(value.items())[:60]
        }
    if isinstance(value, list):
        return {"<array>": len(value), "item": shape(value[0], depth + 1) if value else None}
    if isinstance(value, bool):
        return "<bool>"
    if isinstance(value, (int, float)):
        return "<number>"
    if value is None:
        return "<null>"
    text = str(value)
    if LABEL_RE.match(text) and not EMAIL_RE.search(text):
        return text  # court libellé : utile pour repérer les états (« open », « Ouvert »…)
    return "<string>"


def parse_json_loose(text: str | bytes | None) -> Any | None:
    """JSON, avec tolérance pour les préfixes numériques de type socket.io (« 42["événement",{…}] »)."""
    if text is None:
        return None
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="ignore")
    text = text.strip()
    for candidate in (text, re.sub(r"^\d+", "", text)):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def should_skip_host(host: str | None) -> bool:
    return not host or any(part in host for part in SKIP_HOST_PARTS)


class Recorder:
    """Garde en mémoire (bornée) la structure observée. Désactivé tant qu'on ne l'allume pas."""

    def __init__(self) -> None:
        self.enabled = False
        self.clear()

    def clear(self) -> None:
        self.snapshots: list[dict] = []
        self.requests: OrderedDict[tuple[str, str], dict] = OrderedDict()
        self.sockets: OrderedDict[str, dict] = OrderedDict()
        self.started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def counts(self) -> dict[str, int]:
        return {"snapshots": len(self.snapshots), "requests": len(self.requests), "sockets": len(self.sockets)}

    # ------------------------------------------------------------------ réseau
    def add_request(
        self,
        method: str,
        url: str,
        status: int | None,
        resource_type: str,
        request_header_names: list[str],
        request_body: str | None,
        response_content_type: str,
        response_body: str | bytes | None,
    ) -> None:
        masked = mask_url(url)
        host_and_path = masked.split("?")[0]
        key = (method, host_and_path)
        entry = self.requests.get(key)
        if entry is None:
            if len(self.requests) >= MAX_REQUESTS:
                return
            entry = self.requests[key] = {
                "method": method,
                "url": host_and_path,
                "type": resource_type,
                "count": 0,
                "statuses": [],
                "query_params": [],
                "request_headers": [],
                "request_shape": None,
                "response_content_type": response_content_type.split(";")[0],
                "response_shape": None,
            }
        entry["count"] += 1
        if status is not None and status not in entry["statuses"]:
            entry["statuses"].append(status)
        if "?" in masked:
            for name in masked.split("?", 1)[1].split("&"):
                if name and name not in entry["query_params"]:
                    entry["query_params"].append(name)
        for name in request_header_names:
            if name.lower() not in entry["request_headers"]:
                entry["request_headers"].append(name.lower())
        if entry["request_shape"] is None and request_body:
            parsed = parse_json_loose(request_body)
            entry["request_shape"] = shape(parsed) if parsed is not None else f"<non-JSON, {len(request_body)} caractères>"
        if entry["response_shape"] is None and response_body:
            parsed = parse_json_loose(response_body)
            if parsed is not None:
                entry["response_shape"] = shape(parsed)

    def add_ws_frame(self, url: str, direction: str, payload: str | bytes) -> None:
        masked = mask_url(url).split("?")[0]
        sock = self.sockets.get(masked)
        if sock is None:
            if len(self.sockets) >= 20:
                return
            sock = self.sockets[masked] = {"url": masked, "sent": {}, "received": {}}
        bucket = sock[direction]
        parsed = parse_json_loose(payload)
        if parsed is not None:
            prefix = re.match(r"^\d+", payload.decode("utf-8", "ignore") if isinstance(payload, bytes) else payload)
            description: Any = {"prefix": prefix.group(0) if prefix else "", "shape": shape(parsed)}
        elif isinstance(payload, bytes):
            description = f"<binaire, {len(payload)} octets>"
        else:
            description = f"<texte, {len(payload)} caractères>"
        key = json.dumps(description, sort_keys=True, ensure_ascii=False)
        if key in bucket:
            bucket[key]["count"] += 1
        elif len(bucket) < MAX_SHAPES_PER_DIRECTION:
            bucket[key] = {"count": 1, "frame": description}

    # ------------------------------------------------------------------- pages
    def add_snapshot(self, url: str, snapshot: dict) -> bool:
        """Retourne False quand la limite de pages est atteinte (rien n'est alors enregistré)."""
        if len(self.snapshots) >= MAX_SNAPSHOTS:
            return False
        elements = []
        for el in snapshot.get("elements", []):
            clean = {k: (mask_text(v, 80) if isinstance(v, str) else v) for k, v in el.items()}
            elements.append(clean)
        texts = [{"path": t.get("path", ""), "text": mask_text(t.get("text", ""), 80)} for t in snapshot.get("texts", [])]
        self.snapshots.append(
            {
                "page": mask_url(url).split("?")[0],
                "title": mask_text(snapshot.get("title", ""), 80),
                "viewport": snapshot.get("viewport"),
                "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "elements": elements,
                "texts": texts,
            }
        )
        return True

    # ------------------------------------------------------------------ export
    def export(self, cookies: list[dict], storage: dict[str, list[str]]) -> dict:
        return {
            "meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "recording_started": self.started,
                "note": "Structure uniquement : valeurs, jetons, cookies et saisies sont masqués ou absents.",
            },
            "cookies": [
                {
                    **{k: c.get(k) for k in ("domain", "path", "httpOnly", "secure", "sameSite")},
                    "name": mask_key(c.get("name", "")),
                }
                for c in cookies
            ],
            "storage_keys": {kind: [mask_key(k) for k in keys] for kind, keys in storage.items()},
            "pages": self.snapshots,
            "requests": list(self.requests.values()),
            "websockets": [
                {
                    "url": s["url"],
                    "sent": list(s["sent"].values()),
                    "received": list(s["received"].values()),
                }
                for s in self.sockets.values()
            ],
        }
