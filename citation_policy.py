"""Literal Citation link policy. No bibliography or network access."""
import re
from urllib.parse import urlsplit
from mwparserfromhell.nodes import Comment, ExternalLink, Text

CANDIDATES = {
    'wikirouge.net': 'wikirouge', 'fr.wikisource.org': 'wikisource',
    'wikisource.org': 'wikisource', 'eur-lex.europa.eu': 'eurlex',
    'vatican.va': 'vatican', 'un.org': 'un', 'documents.un.org': 'un',
    'digitallibrary.un.org': 'un', 'hudoc.echr.coe.int': 'echr',
    'normlex.ilo.org': 'ilo', 'laws-lois.justice.gc.ca': 'canada',
}
BARE = re.compile(r'(?:(?:https?://|//)?(?:[A-Za-z0-9-]+\.)+[A-Za-z0-9-]+)(?::[0-9]+)?(?:[/?#][^\s<>{}\[\]|"\x27]*)?', re.I)
HOST = re.compile(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+\Z')


def web_url(value):
    value = value.strip()
    if not BARE.fullmatch(value):
        return None
    if value.startswith('//'):
        value = 'https:' + value
    elif not re.match(r'^https?://', value, re.I):
        value = 'https://' + value
    try:
        p = urlsplit(value)
        if (p.scheme not in ('http', 'https') or not p.hostname or p.username
                or p.password or p.port not in (None, 80, 443) or not HOST.fullmatch(p.hostname)):
            return None
        return value, p.hostname
    except ValueError:
        return None


def domain(host):
    return host[4:] if host.startswith('www.') else host


def link_policy(template):
    params = [p.value for p in template.params if str(p.name).strip() == 'lien']
    if not params:
        return {'route': 'ai', 'reason': 'missing_link', 'domains': []}
    if len(params) != 1:
        return {'route': 'ai', 'reason': 'duplicate_link_parameter', 'domains': []}
    nodes = [n for n in params[0].nodes if not isinstance(n, Comment)]
    literal = ''.join(str(n) for n in nodes).strip()
    if not literal:
        return {'route': 'ai', 'reason': 'empty_link', 'domains': []}
    links = []
    complex_field = False
    if all(isinstance(n, Text) for n in nodes):
        parsed = web_url(literal)
        if parsed:
            links.append(parsed)
        else:
            complex_field = True
    else:
        for n in nodes:
            if isinstance(n, ExternalLink):
                parsed = web_url(str(n.url))
                if parsed:
                    links.append(parsed)
                else:
                    complex_field = True
            elif not isinstance(n, Text) or str(n).strip():
                complex_field = True
    result = {'route': 'ai', 'domains': sorted({domain(h) for _, h in links})}
    if complex_field or len(links) != 1:
        return {**result, 'reason': 'ambiguous_or_nonliteral_link'}
    url, host = links[0]
    if host in ('marxists.org', 'www.marxists.org'):
        from navigation import canonical_mia
        source = canonical_mia(url)
        if source:
            return {**result, 'route': 'mia', 'reason': 'explicit_mia_link', 'mia_url': source}
        return {**result, 'reason': 'unsupported_mia_url'}
    candidate = CANDIDATES.get(domain(host))
    if candidate:
        return {**result, 'reason': 'provider_not_enabled', 'candidate': candidate}
    return {**result, 'reason': 'other_domain'}
