"""
Nettoyage HTML côté serveur pour les descriptions produit.

Le nettoyage du navigateur est un confort d'édition, pas une sécurité: un client
peut poster n'importe quel HTML directement sur l'API. Toute description est donc
repassée ici avant enregistrement, et c'est cette version qui part en base.

Sans dépendance externe: s'appuie sur html.parser de la bibliothèque standard.
"""

from html.parser import HTMLParser
from html import escape
from typing import Optional
import re

# Balises conservées. Tout le reste est retiré, mais son contenu texte est gardé.
ALLOWED_TAGS = {
    "p", "br", "b", "strong", "i", "em", "u", "s", "strike", "sub", "sup",
    "ul", "ol", "li", "a", "img", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "code", "div", "span", "hr",
    "table", "thead", "tbody", "tr", "th", "td",
}

# Balises sans contenu: elles ne doivent jamais recevoir de fermeture.
VOID_TAGS = {"br", "img", "hr"}

# Balises dont le contenu lui-même est supprimé (et pas seulement la balise).
DROP_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "noscript", "template"}

ALLOWED_ATTRS = {"href", "src", "alt", "title", "style", "target", "rel", "class", "colspan", "rowspan"}

ALLOWED_STYLES = {
    "font-size", "font-family", "font-weight", "font-style", "text-decoration",
    "color", "background-color", "text-align", "line-height", "margin-left",
}

_SAFE_URL = re.compile(r"^(https?://|mailto:|tel:|/|#)", re.I)
_DANGEROUS_CSS = re.compile(r"(expression|javascript:|behavior|@import|url\s*\()", re.I)
# Les classes servent à la mise en forme; on refuse tout ce qui sort de l'alphanumérique.
_SAFE_CLASS = re.compile(r"^[A-Za-z0-9 _-]{0,200}$")


def _is_safe_url(value: str) -> bool:
    return bool(value) and bool(_SAFE_URL.match(value.strip()))


def _clean_style(value: str) -> str:
    kept = []
    for declaration in (value or "").split(";"):
        if ":" not in declaration:
            continue
        name, _, raw = declaration.partition(":")
        name = name.strip().lower()
        raw = raw.strip()
        if not name or not raw:
            continue
        if name not in ALLOWED_STYLES:
            continue
        if _DANGEROUS_CSS.search(raw):
            continue
        kept.append(f"{name}: {raw}")
    return "; ".join(kept)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.skip_depth = 0          # profondeur dans une balise dont le contenu est jeté
        self.skip_tag: Optional[str] = None

    # -------------------------------------------------------------- balises

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if self.skip_depth:
            if tag == self.skip_tag:
                self.skip_depth += 1
            return

        if tag in DROP_CONTENT_TAGS:
            self.skip_depth = 1
            self.skip_tag = tag
            return

        if tag not in ALLOWED_TAGS:
            return  # balise retirée, son contenu textuel reste

        cleaned = []
        for name, value in attrs:
            name = (name or "").lower()
            value = value or ""

            # Tout gestionnaire d'évènement est écarté d'office.
            if name.startswith("on") or name not in ALLOWED_ATTRS:
                continue

            if name == "href":
                if not _is_safe_url(value):
                    continue
                cleaned.append(("href", value.strip()))
            elif name == "src":
                if not _is_safe_url(value):
                    continue
                cleaned.append(("src", value.strip()))
            elif name == "style":
                style = _clean_style(value)
                if style:
                    cleaned.append(("style", style))
            elif name == "class":
                if _SAFE_CLASS.match(value):
                    cleaned.append(("class", value))
            elif name in ("colspan", "rowspan"):
                if value.isdigit() and int(value) <= 100:
                    cleaned.append((name, value))
            elif name in ("target", "rel"):
                continue  # imposés ci-dessous pour les liens
            else:
                cleaned.append((name, value))

        # Un lien externe ne doit pas pouvoir manipuler l'onglet d'origine.
        if tag == "a" and any(n == "href" for n, _ in cleaned):
            cleaned.append(("target", "_blank"))
            cleaned.append(("rel", "noopener noreferrer"))

        rendered = "".join(f' {n}="{escape(v, quote=True)}"' for n, v in cleaned)

        if tag in VOID_TAGS:
            self.parts.append(f"<{tag}{rendered}>")
        else:
            self.parts.append(f"<{tag}{rendered}>")
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in VOID_TAGS or tag in ALLOWED_TAGS:
            self.handle_starttag(tag, attrs)
            if tag not in VOID_TAGS and self.open_tags and self.open_tags[-1] == tag:
                self.open_tags.pop()
                self.parts.append(f"</{tag}>")

    def handle_endtag(self, tag):
        tag = tag.lower()

        if self.skip_depth:
            if tag == self.skip_tag:
                self.skip_depth -= 1
                if self.skip_depth == 0:
                    self.skip_tag = None
            return

        if tag in VOID_TAGS or tag not in ALLOWED_TAGS:
            return
        if tag not in self.open_tags:
            return

        # Referme aussi les balises restées ouvertes à l'intérieur.
        while self.open_tags:
            current = self.open_tags.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    # -------------------------------------------------------------- contenu

    def handle_data(self, data):
        if self.skip_depth:
            return
        self.parts.append(escape(data, quote=False))

    def handle_comment(self, data):
        pass  # les commentaires sont supprimés

    def unknown_decl(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    # -------------------------------------------------------------- sortie

    def result(self) -> str:
        while self.open_tags:
            self.parts.append(f"</{self.open_tags.pop()}>")
        return "".join(self.parts)


def sanitize_html(value: Optional[str], max_length: int = 100_000) -> str:
    """Renvoie une version sûre du HTML fourni. Chaîne vide si l'entrée est vide."""
    if not value:
        return ""
    if len(value) > max_length:
        value = value[:max_length]

    parser = _Sanitizer()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        # En cas d'analyse impossible, on retombe sur du texte échappé: jamais de HTML brut.
        return escape(value, quote=False)
    return parser.result()


def html_to_text(value: Optional[str], limit: int = 300) -> str:
    """Version texte d'une description HTML, pour les aperçus et méta-descriptions."""
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", " ", value, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def render_description(value: Optional[str]) -> str:
    """
    Prépare une description pour l'affichage public.

    Les descriptions saisies avant l'éditeur enrichi sont du texte brut: les
    rendre telles quelles en HTML écraserait leurs retours à la ligne. On les
    échappe et on restitue les sauts de ligne; le HTML, lui, est nettoyé.
    """
    if not value:
        return ""
    looks_like_html = bool(re.search(r"<[a-zA-Z/][^>]*>", value))
    if looks_like_html:
        return sanitize_html(value)
    return escape(value, quote=False).replace("\r\n", "\n").replace("\n", "<br>")
