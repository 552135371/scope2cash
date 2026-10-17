"""Deterministic, brief-sensitive heuristics. They power mock mode, act as the fallback when a live model call
fails, and provide the guardrails (price grounding, budget/deadline extraction) used in both modes."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .models import Invoice, Scope

# (pattern, component name, deliverables, hours, group) -- group "adv" goes into its own milestone
COMPONENTS = [
    (r"\b(log ?in|sign ?up|sign-?in|auth|accounts?|user profiles?|members? (?:can|should) (?:sign|log)|registro|cadastro|inicio de sesi)", "User accounts & authentication", ["Email/social sign-in", "Profile & settings"], 10, "core"),
    (r"\b(payments?|stripe|paypal|checkout|subscriptions?|billing|class packs?|membership|paywall|pagos?|pagamentos?|tarjeta|cart[aã]o)", "Payments & subscriptions", ["Checkout flow", "Subscription plans", "Receipts"], 14, "adv"),
    (r"\b(dashboard|admin|back ?office|analytics|reports?|panel|painel|progress tracking)\b", "Admin dashboard & analytics", ["Admin panel", "Key metrics view"], 16, "core"),
    (r"\b(chatbot|ai\b|llm|gpt|assistant|agent|recommendations?|classif)", "AI assistant integration", ["LLM prompt design", "Chat UI with streaming", "Guardrails & evaluation set"], 18, "adv"),
    (r"\b(booking|book (?:or|a|class)|books appointments|calendar|appointments?|schedul|reservations?|reservas?|agendamento)", "Booking & calendar", ["Availability calendar", "Booking confirmation flow"], 14, "core"),
    (r"\b(notifications?|push|reminders?|sms|notificaci\w+|notifica\w+)\b", "Notifications", ["Transactional emails", "Reminder triggers"], 6, "core"),
    (r"\b(e-?commerce|online shop|store front|storefront|shopping cart|cart|product catalog|shopify|woocommerce|carrito|carrinho|pedidos|orders?|men[uú]|listings?|marketplace)", "E-commerce catalog & cart", ["Product catalog", "Cart & order flow"], 18, "core"),
    (r"\b(maps?|gps|geolocation|location tracking|mapa)\b", "Maps & location", ["Map view", "Location search"], 10, "core"),
    (r"\b(chat|messaging|inbox|real-?time)\b", "Real-time messaging", ["1:1 chat", "Read receipts & presence"], 16, "core"),
    (r"\b(cms|blog|content management|articles)\b", "CMS & content", ["Content model", "Editor workflow"], 8, "core"),
    (r"\b(multi-?lingual|multi-?language|translations?|i18n|localization|bilingual)", "Localization", ["String extraction", "2 languages"], 6, "core"),
    (r"\b(video|stream(?:ing)?|live class)", "Video", ["Video upload & playback"], 12, "core"),
    (r"\b(offline|sync)\b", "Offline sync", ["Local cache", "Conflict handling"], 10, "core"),
    (r"\b(upload|attachments?|files?|documents?|pdf)\b", "File handling", ["Uploads & storage", "PDF export"], 6, "core"),
    (r"\b(reviews?|ratings?)\b", "Reviews & ratings", ["Review submission", "Moderation"], 6, "core"),
    (r"\b(integrat|zapier|crm|hubspot|salesforce|airtable|google sheets|notion|slack|webhook|api)\b", "Third-party integrations", ["API integration", "Data sync"], 10, "core"),
    (r"\b(contact forms?|lead forms?|sign-?up forms?|quote forms?)\b", "Forms & lead capture", ["Contact form with spam protection", "Email notification"], 4, "core"),
    (r"\b(landing page|marketing site|website|seo|wordpress|webflow|\d+-page)\b", "Marketing website", ["Responsive landing page", "Basic SEO setup"], 12, "core"),
]
DESIGN = [
    (r"\b(logo|logotype|wordmark)", "Logo design", ["3 initial concepts", "2 refinement rounds", "Final vector files (SVG/PDF/PNG)"], 14),
    (r"\b(brand (?:guide|book|kit|identity)|branding|visual identity|style guide)", "Brand guidelines", ["Color palette & typography", "Usage guide PDF"], 10),
    (r"\b(social media|instagram|templates?|posts?)\b", "Social media kit", ["6 post templates", "Story templates"], 6),
    (r"\b(business cards?|stationery|letterhead|packaging|labels?|menu)\b", "Print collateral", ["Business card", "Print-ready files"], 6),
    (r"\b(figma|ui design|ux|wireframes?|mockups?|prototype)\b", "UI/UX design", ["Key screen mockups", "Clickable Figma prototype"], 16),
    (r"\b(illustrations?|icons?|mascot|character)\b", "Illustration set", ["Mascot/illustrations", "Icon set"], 12),
]
DATA = [
    (r"\b(scrap\w*|crawl\w*|extract\w*|harvest)", "Scraper build", ["Site parsers", "Pagination & rate limiting"], 14),
    (r"\b(proxy|proxies|captcha|cloudflare|anti-?bot|login wall|javascript-?heavy|dynamic pages?)", "Anti-bot handling", ["Headless browser fallback", "Proxy rotation"], 10),
    (r"\b(schedul\w*|daily|weekly|hourly|cron|monitor\w*)", "Scheduling & monitoring", ["Scheduled runs", "Failure alerts"], 6),
    (r"\b(csv|excel|google sheets|database|postgres|airtable|json|export)\b", "Data cleaning & export", ["Deduplication & normalisation", "CSV/Sheets export"], 8),
    (r"\b(dashboard|chart|report|analysis|insights)\b", "Reporting", ["Summary dashboard"], 10),
]
AUTOMATION = [
    (r"\b(crm|hubspot|salesforce|pipedrive|airtable)\b", "CRM sync", ["Contact & deal sync", "Field mapping"], 6),
    (r"\b(gmail|email|inbox|outlook)\b", "Email intake & parsing", ["Inbox trigger", "Parsing rules"], 5),
    (r"\b(slack|teams|discord|telegram|whatsapp)\b", "Chat alerts", ["Channel notifications"], 3),
    (r"\b(ai|gpt|llm|classif\w*|summar\w*|openai)\b", "AI classification step", ["Prompted classifier", "Fallback rules"], 6),
    (r"\b(invoice|quickbooks|xero|stripe|paypal)\b", "Accounting hand-off", ["Invoice creation step"], 5),
    (r"\b(google sheets|sheets|spreadsheet|notion)\b", "Sheets/Notion logging", ["Row logging", "Daily digest"], 3),
    (r"\b(calendly|calendar|booking)\b", "Calendar booking", ["Booking webhook"], 3),
]
CREEP = [
    (r"\b(like|similar to|clone of)\s+(uber|airbnb|facebook|instagram|tiktok|amazon|netflix|whatsapp|tinder)", "Brief compares the product to {m}; agree a written MVP feature list so the quote stays bounded"),
    (r"\b(etc\.?|and so on|and more|anything else|whatever)\b", "Open-ended wording ('{m}'); lock the feature list before quoting a fixed price"),
    (r"\bunlimited (revisions?|changes?|pages?)", "Client asks for unlimited {m}; cap rounds in the contract"),
    (r"\b(maybe also|might also|later we|maybe later|phase 2|nice to have|bonus)\b", "Possible add-ons mentioned ('{m}'); price them as optional milestones"),
    (r"\b(asap|urgent|yesterday|in a week|this weekend)\b", "Very tight timeline ('{m}'); add a rush fee or cut scope"),
    (r"\b(cheap|low budget|tight budget|small budget|not much money|simple app)\b", "Budget sensitivity ('{m}'); offer a reduced-scope option"),
    (r"\b(secure|bank statements?|financial records|medical|health info\w*|passports?|ssn)\b", "Sensitive data ('{m}'); agree on encryption, access control and retention up front"),
    (r"\b(hipaa|gdpr|pci|soc ?2|phi|patient data)\b", "Compliance requirement ({m}); confirm hosting, BAA/DPA and data handling before quoting"),
]
LANG_HINTS = {
    "es": r"\b(hola|necesito|queremos|aplicación|para|también|gracias|precio)\b",
    "pt": r"\b(olá|preciso|queremos|aplicativo|também|obrigad[oa]|orçamento)\b",
    "de": r"\b(hallo|wir brauchen|möchten|eine app|bitte|danke|und auch|kunden)\b",
    "fr": r"\b(bonjour|nous voulons|besoin|application|merci|aussi|devis)\b",
}
LANG_NAMES = {"es": "Spanish", "pt": "Portuguese", "de": "German", "fr": "French", "zh": "Chinese", "ja": "Japanese", "en": "English"}
MONTHS = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
TYPE_PHRASE = {"ios": "iOS app", "mobile": "mobile app", "automation": "automation workflow", "web": "web app",
               "design": "brand identity", "data": "data scraping pipeline"}


def detect_language(text: str) -> str:
    if len(re.findall(r"[\u4e00-\u9fff]", text)) > 8:
        return "zh"
    if len(re.findall(r"[\u3040-\u30ff]", text)) > 8:
        return "ja"
    t = text.lower()
    scores = {k: len(re.findall(p, t)) for k, p in LANG_HINTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else "en"


def detect_type(text: str) -> str:
    t = text.lower()
    builds_app = re.search(r"\b(app|application|aplicación|aplicativo|platform|saas|portal|website|web site|backend|api)\b", t)
    if re.search(r"\b(android|react native|flutter|cross-platform|aplicativo|aplicaci[oó]n m[oó]vil|app m[oó]vil|app mobile)\b", t):
        return "mobile"
    if re.search(r"\b(ios|iphone|ipad|swift|swiftui|app store)\b", t):
        return "ios"
    if re.search(r"\bmobile app\b", t):
        return "mobile"
    if re.search(r"\b(n8n|zapier|make\.com|automat\w*|workflows?)\b", t) and not re.search(r"\b(app|platform|saas)\b", t):
        return "automation"
    if re.search(r"\b(scrap\w*|crawl\w*|data pipeline|etl|dataset|extract (?:data|prices|listings))", t) and not builds_app:
        return "data"
    if re.search(r"\b(logo|branding|brand identity|brand kit|visual identity|illustration|business cards?|packaging)\b", t) and not builds_app:
        return "design"
    return "web"


def client_name(text: str) -> str:
    pats = [r"(?i:I'm|I am|this is|it's)\s+[A-Z]\w+\s+(?:from|at|with)\s+([A-Z][\w&'\- ]{1,30})",
            r"(?:We are|We're|we run)\s+(?:an?\s+)?([A-Z][\w&'\- ]{1,30})",
            r"(?:company|startup|studio|clinic|agency|shop|bakery|business|brand)\s+(?:called|named)\s+[\"']?([A-Z][\w&'\- ]{1,30})",
            r"(?:Soy|soy)\s+\w+\s+de\s+([A-Z][\w&'\- ]{1,30})"]
    for pat in pats:
        m = re.search(pat, text)
        if m:
            name = re.split(r"\s+(?:in|from|at|based|and|we|our|located|en|em|de|na|no)\b|[.,;!(]", m.group(1).strip())[0]
            name = name.strip(" .,'\"")
            if name and name.lower() not in ("a", "an", "the", "looking", "building"):
                return name
    return "Client"


def _parse_amount(raw: str, k: str) -> Optional[float]:
    raw = raw.rstrip(".,")
    try:
        if re.search(r"[.,]\d{3}$", raw) and not re.search(r"\.\d{2}$", raw):
            val = float(raw.replace(".", "").replace(",", ""))  # 5.000 / 5,000
        else:
            val = float(raw.replace(",", ""))
    except ValueError:
        return None
    return val * 1000 if k else val


_AMT = r"(?:usd|us\$|\$|€|eur|£)?\s?(\d[\d,.]*)\s?(k|mil)?"


def extract_budget(text: str) -> Optional[float]:
    """Only budgets the client actually states. Ranges ('$15-20k') return the upper bound."""
    t = text.replace("\u00a0", " ")
    pats = [rf"(?:budget|presupuesto|or[cç]amento)[^.$€£\d]{{0,40}}?{_AMT}(?:\s?(?:-|–|to|a)\s?{_AMT})?",
            rf"(?:\$|€|£|usd\s?)(\d[\d,.]*)\s?(k)?(?:\s?(?:-|–|to)\s?\$?(\d[\d,.]*)\s?(k)?)?\s*(?:budget|max|total|tops|for (?:the|this) (?:whole|project|job))"]
    for pat in pats:
        m = re.search(pat, t, re.I)
        if not m:
            continue
        lo_raw, lo_k, hi_raw, hi_k = m.group(1), m.group(2), m.group(3), m.group(4)
        val = None
        if hi_raw:
            val = _parse_amount(hi_raw, hi_k)
            lo = _parse_amount(lo_raw, lo_k or hi_k)
            if lo and val and lo > val:
                val = lo
        else:
            val = _parse_amount(lo_raw, lo_k)
        if val and 50 <= val <= 1_000_000:
            return val
    return None


def extract_deadline(text: str) -> Optional[str]:
    t = text.lower()
    for pat in [rf"\b(?:by|before|until|launch(?:ing)? in|ready (?:by|for)|deadline(?: is)?:?)\s+((?:the\s+)?(?:end of\s+)?(?:{MONTHS}|next \w+|\d{{1,2}}/\d{{1,2}}|q[1-4]|black friday|christmas|summer|spring|fall|autumn)(?:\s+\d{{1,2}}\b)?)",
                rf"\b(?:in|within)\s+(\d+\s+(?:weeks?|days?|months?))", rf"\b(?:launch|promo|ready|live|done)\b[^.]{{0,40}}?\bin\s+({MONTHS})\b", rf"\b({MONTHS}\s+\d{{1,2}})\b", r"\b(asap|urgent)\b"]:
        m = re.search(pat, t)
        if m:
            return m.group(1).strip()
    return None


def _first_sentence(text: str) -> str:
    """The most informative sentence: the one mentioning the most scoped features (ties -> earliest)."""
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if len(x.split()) >= 5]
    sentences = [x for x in sentences if not re.match(r"(?i)^(hi|hello|hey|dear|hola|bonjour|hallo)\b[!,. ]*$", x)] or sentences
    if not sentences:
        return text[:220]
    pats = [c[0] for c in COMPONENTS] + [c[0] for c in DESIGN] + [c[0] for c in DATA] + [c[0] for c in AUTOMATION]
    def score(x: str) -> int:
        return sum(1 for p in pats if re.search(p, x.lower()))
    goal = next((x for x in sentences if re.search(r"(?i)\b(need|want|looking for|necesito|queremos|build(?:ing)?|opening)\b", x)), None)
    best = max(sentences, key=lambda x: (score(x), -sentences.index(x)))
    if goal and goal != best and len(goal.split()) < 14:
        best = goal + " " + best
    elif goal and score(goal) >= score(best) - 1:
        best = goal
    best = re.sub(r"(?i)^(hi|hello|hey|hola)[!,.]?\s+", "", best)
    return best[:240] + ("..." if len(best) > 240 else "")


def contact_name(text: str) -> Optional[str]:
    m = re.search(r"(?i:I'm|I am|this is|it's|soy|sou|je suis|ich bin)\s+([A-Z][a-zà-ÿ]+)\b", text)
    return m.group(1) if m else None


def project_noun(text: str) -> Optional[str]:
    m = re.search(r"\b(?:for|para)\s+(?:our\s+|my\s+)?([a-z][a-z\- ]{2,24}?)(?=[:.,!?]|\s+(?:where|that|with|who|and|so|in)\b)", text.lower())
    if not m:
        return None
    noun = m.group(1).strip()
    if noun in ("members", "us", "me", "the app", "our members", "it") or len(noun) < 3:
        return None
    return noun


def pretty_deadline(d: Optional[str]) -> str:
    if not d:
        return ""
    d = re.sub(r"^the\s+", "", d.strip())
    return " ".join(w.capitalize() if re.match(MONTHS + "$", w) or w in ("q1", "q2", "q3", "q4") else w for w in d.split()).replace("Q", "Q")


def route_scope_tier(text: str) -> Tuple[str, str]:
    """Nano handles routine extraction; long or non-English briefs escalate to Super."""
    if len(text) > 2500:
        return "main", "long brief -> escalated to main tier"
    if detect_language(text) != "en":
        return "main", "non-English brief -> escalated to main tier"
    return "fast", "routine brief -> fast tier"


def mock_scope(text: str) -> Dict[str, Any]:
    t = text.lower()
    words = len(text.split())
    ptype = detect_type(text)
    lang = detect_language(text)
    client = client_name(text)
    budget, deadline = extract_budget(text), extract_deadline(text)
    risks: List[str] = []
    for pat, msg in CREEP:
        m = re.search(pat, t)
        if m:
            word = (m.group(m.lastindex) if m.lastindex else m.group(0)).strip()
            risks.append(msg.format(m=word.capitalize() if "compares" in msg else word))

    if ptype == "design":
        comps = [(n, d, h) for p, n, d, h in DESIGN if re.search(p, t)] or [DESIGN[0][1:]]
        ms = [{"title": "Discovery & moodboard", "deliverables": ["Brand questionnaire", "Competitor scan", "Moodboard"], "est_hours": 5, "weeks": 0.5}]
        ms += [{"title": n, "deliverables": d, "est_hours": h, "weeks": 1} for n, d, h in comps[:3]]
        if len(comps) > 3:
            ms.append({"title": "Additional assets", "deliverables": [x for c in comps[3:] for x in c[1]], "est_hours": sum(c[2] for c in comps[3:]), "weeks": 1})
        ms.append({"title": "Final files & handover", "deliverables": ["Export package", "Source files", "Usage walkthrough"], "est_hours": 3, "weeks": 0.5})
        assumptions = ["Client supplies brand name, tagline and any existing assets", "2 revision rounds per deliverable",
                       "Font licences, if any, are purchased by the client"]
    elif ptype == "data":
        comps = [(n, d, h) for p, n, d, h in DATA if re.search(p, t)] or [DATA[0][1:]]
        n_sites = re.search(r"(\d+)\s+(?:[\w-]+\s+){0,2}(?:sites|websites|sources|marketplaces|stores|shops)\b", t)
        mult = min(int(n_sites.group(1)), 10) if n_sites else 1
        ms = [{"title": "Source audit & data spec", "deliverables": ["Field list & sample output", "robots.txt / ToS review", "Feasibility notes"], "est_hours": 4 + mult, "weeks": 0.5}]
        for n, d, h in comps:
            ms.append({"title": n, "deliverables": d, "est_hours": h + (h * 0.4 * (mult - 1) if n == "Scraper build" else 0), "weeks": 1})
        ms.append({"title": "Handover & 30-day fixes", "deliverables": ["Run guide", "Selector fix window"], "est_hours": 4, "weeks": 0.5})
        assumptions = ["Only publicly available data is collected, respecting site terms", "Proxy / captcha-solving fees billed to client",
                       f"{mult} source site(s) in scope; more are a change request"]
        risks.append("Target sites can change layout or block scraping; maintenance is billed separately")
    elif ptype == "automation":
        comps = [(n, d, h) for p, n, d, h in AUTOMATION if re.search(p, t)] or [("Core workflow", ["Trigger & routing logic"], 6)]
        ms = [{"title": "Process mapping", "deliverables": ["Current-state flow", "Trigger/action spec", "Credentials checklist"], "est_hours": 3, "weeks": 0.5},
              {"title": "Workflow build", "deliverables": [x for c in comps for x in c[1]][:6] + ["Error handling & retries"], "est_hours": 4 + sum(c[2] for c in comps), "weeks": 1},
              {"title": "Testing & monitoring", "deliverables": ["Test runs with real data", "Failure alerts"], "est_hours": 4, "weeks": 0.5},
              {"title": "Handover", "deliverables": ["Loom walkthrough", "Runbook", "14-day support"], "est_hours": 2, "weeks": 0.5}]
        assumptions = ["Client provides admin access to every connected tool", "Hosting on client's n8n/Zapier plan; subscription fees billed to client",
                       "Up to 2 change rounds after test runs"]
    else:
        comps = [(n, d, h, g) for p, n, d, h, g in COMPONENTS if re.search(p, t)]
        if not comps:
            comps = [("Core feature build", ["Main user flow", "Data model & storage"], 24, "core")]
        platform = {"ios": ("Native iOS app (SwiftUI)", ["Core screens in SwiftUI", "Local persistence", "TestFlight build"], 30),
                    "mobile": ("Cross-platform mobile app", ["Core screens", "Local persistence", "Store builds"], 30),
                    "web": ("Web app foundation", ["Responsive UI shell", "Backend API & database"], 18)}[ptype]
        core = [platform] + [c[:3] for c in comps if c[3] == "core"]
        adv = [c[:3] for c in comps if c[3] == "adv"]
        ms = [{"title": "Discovery & UX design", "deliverables": ["Requirements sign-off", "Clickable wireframes", "Technical plan"],
               "est_hours": 8 + 2 * min(len(comps), 4), "weeks": 1}]
        core_hours = sum(c[2] for c in core)
        if core_hours > 70:  # split big builds into two milestones so each invoice stays a sensible size
            half = len(core) // 2 + 1
            ms.append({"title": "Core build I: " + core[0][0].split(" (")[0], "deliverables": [d for c in core[:half] for d in c[1]][:6],
                       "est_hours": sum(c[2] for c in core[:half]), "weeks": 2})
            ms.append({"title": "Core build II: " + ", ".join(c[0].split(" ")[0] for c in core[half:half + 3]),
                       "deliverables": [d for c in core[half:] for d in c[1]][:6], "est_hours": sum(c[2] for c in core[half:]), "weeks": 2})
        else:
            ms.append({"title": "Core build", "deliverables": [d for c in core for d in c[1]][:6], "est_hours": core_hours, "weeks": 2})
        if adv:
            ms.append({"title": " & ".join(c[0].split(" ")[0] for c in adv) + " features", "deliverables": [d for c in adv for d in c[1]],
                       "est_hours": sum(c[2] for c in adv), "weeks": 1.5})
        ms.append({"title": "QA, launch & handover", "deliverables": ["Bug-fix pass", "App Store submission" if ptype == "ios" else ("Store submission" if ptype == "mobile" else "Deployment"),
                                                                     "Handover docs & 14-day support"], "est_hours": 8 + len(comps), "weeks": 1})
        comps = [c[:3] for c in comps]
        assumptions = ["Client provides brand assets and copy", "Up to 2 revision rounds per milestone",
                       "Third-party service fees (hosting, APIs, app stores) billed to client"]
        if adv and any("AI" in c[0] for c in adv):
            risks.append("AI answer quality is subjective; agree acceptance examples up front")
        if any("Payments" in c[0] for c in adv) and ptype in ("ios", "mobile") and re.search(r"subscription|membership|paywall|class packs?|premium", t):
            risks.append("Digital subscriptions sold in-app must use Apple/Google in-app purchase (15-30% store fee)")

    specificity = len(comps) + (budget is not None) + (deadline is not None)
    vague = words < 25 or (specificity <= 1 and words < 60)
    confidence = round(max(0.25, min(0.9, 0.35 + 0.01 * min(words, 120) * 0.4 + 0.06 * len(comps) - 0.05 * len(risks))), 2)
    if vague:
        confidence = min(confidence, 0.45)
    questions: List[str] = []
    if vague:
        questions += ["Which 3 features matter most for the first version?", "Can you share 1-2 products you like as a reference?",
                      "Who are the users, and roughly how many do you expect at launch?"]
    if not deadline:
        questions.append("Is there a launch date or event we should plan around?")
    if budget is None:
        questions.append("Do you have a budget range in mind?")
    if ptype in ("ios", "mobile", "web"):
        questions.append("Who will own the hosting, app-store and third-party accounts?")
    if ptype == "design":
        questions.append("Where will the logo be used most (app icon, signage, packaging)?")
    if ptype == "data":
        questions.append("How often should the data refresh, and where should it land?")
    if ptype == "automation":
        questions.append("Which tool is the source of truth if two systems disagree?")
    if any("compares the product" in r for r in risks):
        questions.append("Which parts of the reference product are must-haves for v1?")
    if lang != "en":
        questions.insert(0, f"Should the product itself be in {LANG_NAMES.get(lang, lang)}, English, or both?")
        assumptions = assumptions + [f"Brief is partly in {LANG_NAMES.get(lang, lang)}; requirements confirmed in writing before kickoff"]
    if vague:
        assumptions = ["Estimate is a placeholder until a short paid discovery call"] + assumptions
        risks.append("Brief is thin; hours could move ±40% after discovery")
    if deadline:
        assumptions.append(f"Target date mentioned by client: {pretty_deadline(deadline)}")
    if re.search(r"\b(payments?|stripe|paypal|checkout|billing|pagos?)\b", t) and not any("in-app purchase" in r for r in risks):
        risks.append("Payment-provider onboarding (KYC) can take days; start it during milestone 1")
    if not risks:
        risks.append({"automation": "Upstream tools changing their APIs or field names can silently break the workflow; monitoring is included",
                      "design": "Taste is subjective; agree on 2-3 reference brands before the first concepts",
                      "data": "Target sites can change layout or block scraping",
                      }.get(ptype, "Late content (copy, photos, accounts) is the most common cause of launch delays"))
    feats = ", ".join(c[0].split(" & ")[0].split(" (")[0].lower() for c in comps[:4] if c[0] != "Core feature build")
    noun = project_noun(text)
    phrase = TYPE_PHRASE[ptype]
    if ptype == "web" and re.search(r"\b(website|wordpress|webflow|landing page|\d+-page)\b", t) and not re.search(r"\b(app|portal|platform|marketplace|saas)\b", t):
        phrase = "website"
    if client != "Client":
        title = f"{client} {phrase}"
    elif noun:
        title = f"{phrase[0].upper() + phrase[1:]} for {noun}"
    else:
        title = f"Custom {phrase}"
    return {"project_title": title, "client_name": client, "summary": _first_sentence(text) + (f" Key areas: {feats}." if feats else ""),
            "project_type": ptype, "milestones": ms, "assumptions": assumptions, "risks": risks[:5], "open_questions": questions[:6],
            "confidence": confidence, "client_budget": budget, "deadline": pretty_deadline(deadline) or None, "language": lang,
            "contact_name": contact_name(text)}


# ------------------------------------------------------------------ pricing grounding
_MONEY = re.compile(r"(?:US\$|\$|€|£)\s?(\d[\d,]*(?:\.\d+)?)\s?(k|K)?")
_HOURLY = re.compile(r"^\W{0,3}(?:(?:to|-|–|and)\s?(?:US\$|\$|€|£)?\s?\d[\d,]*\s?)?(?:per hour|/\s?h(?:ou)?r?\b|an hour|hourly|p/h|/hour)", re.I)


def money_figures(snippets: List[str]) -> Tuple[List[float], List[float]]:
    """Return (hourly figures, project figures) mentioned in source snippets."""
    hourly, project = [], []
    for s in snippets:
        for m in _MONEY.finditer(s):
            v = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
            after = s[m.end(): m.end() + 30]
            if _HOURLY.search(after) and 10 <= v <= 400:
                hourly.append(v)
            elif v >= 300:
                project.append(v)
    return hourly, project


def mock_pricing(scope: Scope, snippets: List[str], default_rate: float) -> Dict[str, Any]:
    hourly, project = money_figures(snippets)
    if hourly:
        xs = sorted(hourly)
        lo, hi = xs[(len(xs) - 1) // 4], xs[-1 - (len(xs) - 1) // 4]  # interquartile-ish: ignore the extremes
        if hi <= lo:
            lo, hi = xs[0], xs[-1]
        why = f"Sources cite ${lo:.0f}-${hi:.0f}/h for comparable {TYPE_PHRASE.get(scope.project_type, scope.project_type)} work"
    else:
        lo, hi = round(default_rate * 0.8), round(default_rate * 1.4)
        why = f"No hourly figures found in sources, so this range is anchored on your default rate (${default_rate:.0f}/h)"
    if project:
        why += f"; fixed-price examples run ${min(project):,.0f}-${max(project):,.0f}."
    else:
        why += "."
    return {"hourly_low": lo, "hourly_high": hi, "rationale": why}


def ground_pricing(lo: float, hi: float, snippets: List[str]) -> Tuple[float, float, bool, List[str], str]:
    """Guardrail: an hourly range is 'grounded' only if it sits inside the figures the sources actually cite
    (with 20% tolerance). Ungrounded model ranges are pulled back to the evidence and flagged."""
    hourly, project = money_figures(snippets)
    evidence = [f"${v:,.0f}/h" for v in sorted(set(hourly))] + [f"${v:,.0f}" for v in sorted(set(project))][:4]
    if not hourly:
        return lo, hi, False, evidence, "no hourly figures in sources"
    if lo > hi:
        lo, hi = hi, lo
    ev_lo, ev_hi = min(hourly), max(hourly)
    if lo >= ev_lo * 0.8 and hi <= ev_hi * 1.2:
        return lo, hi, True, evidence, ""
    new_lo = ev_lo if lo < ev_lo * 0.8 else lo
    new_hi = ev_hi if hi > ev_hi * 1.2 else hi
    if new_lo >= new_hi:
        new_lo, new_hi = ev_lo, ev_hi
    return new_lo, new_hi, True, evidence, f"model range ${lo:.0f}-${hi:.0f}/h was outside source evidence; clamped to ${new_lo:.0f}-${new_hi:.0f}/h"


# ------------------------------------------------------------------ copy
def _lc(x: str) -> str:
    """Lower-case the first letter unless the word is an acronym (UX, API, iOS)."""
    w = x.split(" ", 1)[0]
    return x if (len(w) > 1 and w[1].isupper()) or w[:1].islower() else x[:1].lower() + x[1:]


def mock_cover(scope: Scope, total: float, rate: float, freelancer: str) -> Dict[str, Any]:
    first = scope.milestones[0]
    hours = sum(m.est_hours for m in scope.milestones)
    who = scope.contact_name or (scope.client_name if scope.client_name != "Client" else "")
    hello = f"Hi {who}," if who else "Hi there,"
    if scope.deadline and scope.deadline.lower() in ("asap", "urgent"):
        deadline = " I can start right away to meet your timeline."
    else:
        deadline = f" The plan is built around your deadline ({pretty_deadline(scope.deadline)})." if scope.deadline else ""
    q = f"\n\nOne question before we start: {scope.open_questions[0][0].lower() + scope.open_questions[0][1:]}" if scope.open_questions else ""
    return {"cover_message": (
        f"{hello}\n\nThanks for the brief. I've scoped \"{scope.project_title}\" as {len(scope.milestones)} milestones, starting with "
        f"{_lc(first.title)} ({', '.join(_lc(d) for d in first.deliverables[:2])}). The total is ${total:,.0f} for about {hours:.0f} hours at "
        f"${rate:.0f}/h.{deadline}\n\nEach milestone gets its own PayPal invoice, so you only pay as work is delivered and approved."
        f"{q}\n\nBest,\n{freelancer}")}


def mock_tips(scope: Scope, total: float, rate: float, budget_gap: Optional[float], cut: Optional[Tuple[str, float]]) -> Dict[str, Any]:
    tips: List[str] = []
    if budget_gap is not None and budget_gap > 0 and cut:
        tips.append(f"The quote is ${budget_gap:,.0f} over their stated budget: offer a version without \"{cut[0]}\" (${total - cut[1]:,.0f}) instead of cutting your rate.")
    elif budget_gap is not None and budget_gap <= 0:
        tips.append("You're within their stated budget, so don't open with a discount; lead with the plan and timeline.")
    if scope.confidence < 0.5:
        tips.append("The brief is thin. Propose milestone 1 as paid discovery and re-quote the rest after it.")
    if any("timeline" in r.lower() for r in scope.risks) or (scope.deadline or "").startswith(("asap", "urgent")):
        tips.append("Their timeline is tight. Quote a 20-25% rush premium or move features to a phase 2.")
    if any("feature list" in r.lower() or "add-ons" in r.lower() for r in scope.risks):
        tips.append("Attach the milestone list as the contract scope; anything outside it becomes a change request at your hourly rate.")
    tips += ["Anchor on outcomes: tie each milestone to a demo the client can click through.",
             "If they push on price, trade scope rather than discounting your rate.",
             "Offer 5% off for paying the first two milestones upfront instead of cutting the total."]
    return {"negotiation_tips": tips[:3]}


def mock_reminder(inv: Invoice, days_overdue: int, freelancer: str) -> Dict[str, Any]:
    tone = "friendly" if days_overdue <= 3 else ("firm" if days_overdue <= 14 else "final")
    opener = {"friendly": "Just a quick nudge", "firm": "Following up", "final": "A final reminder"}[tone]
    return {"subject": f"{opener}: invoice {inv.number} for \"{inv.milestone}\"", "tone": tone,
            "body": (f"Hi,\n\n{opener} on invoice {inv.number} (${inv.amount:,.2f}) for the \"{inv.milestone}\" milestone, "
                     f"which was due on {inv.due_date}" + (f" ({days_overdue} days ago)" if days_overdue > 0 else "") + ".\n\n"
                     f"You can pay securely via PayPal here: {inv.payer_link or '[PayPal link]'}\n\n"
                     + ("If anything about the deliverable needs adjusting, just reply and I'll sort it out right away.\n\n" if tone != "final" else
                        "Work on the next milestone is paused until this is settled. Please let me know if there's an issue I can help with.\n\n")
                     + f"Thanks,\n{freelancer}")}
