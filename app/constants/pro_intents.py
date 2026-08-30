"""Pages written for the artisan who is *buying*, not for their client.

Every SEO page this app generates targets someone looking for a tradesperson:
« plombier Lyon », a company listing, a price guide. An artisan never types any
of that — they type « secrétariat téléphonique plombier » or « qui répond à mes
appels quand je suis sur un chantier ». Two pages on the whole site spoke to
them, /pro and /50-artisans, so the acquisition engine and the thing being sold
pointed at different people.

These fill that gap. They are deliberately few. The repository's own
indexability rules exist because a large set of near-identical pages funnelling
into one signup is a doorway, and the answer to that is not a thinner gate but
fewer, fuller pages: one hub, plus the five call-out trades where a missed call
is an emergency someone else answers within the hour. Every other trade links
into the hub rather than getting a shell of its own.

Each entry carries what makes that trade's phone different — the calls that
come in, when they come in, what has to be written down — so a page is worth
reading on its own, not just worth ranking.
"""
from __future__ import annotations

HUB_SLUG = "secretariat-telephonique-artisan"

# The trades whose page earns its place: an unanswered call is an emergency,
# and the caller is dialling the next number in the results within minutes.
INTENT_TRADES = ("plombier", "serrurier", "electricien", "chauffagiste", "vitrier")

# fr/en payloads per trade:
#   ``h1`` / ``lead``   what the page is and who it is for
#   ``calls``           the calls this trade actually misses, and when
#   ``captured``        what the receptionist has to write down for this trade
#   ``stake``           what one missed call costs, in this trade's terms
#   ``faq``             two questions this trade asks and nobody else does
CONTENT = {
    "plombier": {
        "fr": {
            "h1": "Secrétariat téléphonique pour plombier",
            "lead": "Vous êtes sous un évier, les mains dans l'eau, et le téléphone sonne. "
                    "C'est une fuite chez quelqu'un d'autre — et elle n'attend pas que vous ayez fini.",
            "calls": [
                "Une fuite ou un dégât des eaux, à traiter le jour même.",
                "Un chauffe-eau en panne, souvent un lundi matin ou la veille d'un week-end.",
                "Une canalisation bouchée, avec un client qui appelle trois plombiers d'affilée.",
                "Un devis de salle de bains, qui se décide sur le premier qui rappelle.",
            ],
            "captured": [
                "La nature exacte du problème : fuite, engorgement, panne d'eau chaude.",
                "Si l'eau coule encore, et si le compteur a été fermé.",
                "L'adresse, l'étage et le code d'entrée.",
            ],
            "stake": "Une fuite non traitée part chez le confrère qui a décroché — et avec elle "
                     "l'entretien annuel, le changement de chaudière et la salle de bains d'après.",
            "faq": [
                (
                    "Est-ce que l'assistant sait reconnaître une urgence plomberie ?",
                    "Il pose les questions qui la déterminent — l'eau coule-t-elle encore, le compteur "
                    "est-il fermé, y a-t-il un dégât chez le voisin — et remonte le degré d'urgence avec "
                    "la demande, pour que vous sachiez en un regard ce qui passe avant le reste.",
                ),
                (
                    "Et si l'appel vient d'un syndic ou d'une agence ?",
                    "La demande est notée de la même façon, avec l'interlocuteur, la copropriété et "
                    "l'adresse d'intervention. Vous rappelez avec le dossier déjà constitué.",
                ),
            ],
        },
        "en": {
            "h1": "Phone answering service for plumbers",
            "lead": "You are under a sink with your hands in the water and the phone rings. It is a "
                    "leak at somebody else's place — and it is not waiting for you to finish.",
            "calls": [
                "A leak or water damage, needing someone the same day.",
                "A dead water heater, usually a Monday morning or the day before a weekend.",
                "A blocked drain, from someone ringing three plumbers in a row.",
                "A bathroom quote, won by whoever calls back first.",
            ],
            "captured": [
                "What is actually wrong: leak, blockage, no hot water.",
                "Whether the water is still running and the stopcock is closed.",
                "The address, the floor and the entry code.",
            ],
            "stake": "An unanswered leak goes to whoever picked up — and takes the annual service, the "
                     "boiler replacement and the next bathroom with it.",
            "faq": [
                (
                    "Does the assistant recognise a plumbing emergency?",
                    "It asks the questions that decide it — is the water still running, is the stopcock "
                    "closed, is a neighbour affected — and passes the urgency along with the request, so "
                    "you can see at a glance what comes first.",
                ),
                (
                    "What if the call comes from a managing agent?",
                    "The request is recorded the same way, with the contact, the building and the site "
                    "address. You call back with the file already put together.",
                ),
            ],
        },
    },
    "serrurier": {
        "fr": {
            "h1": "Secrétariat téléphonique pour serrurier",
            "lead": "La porte claquée n'attend pas. Celui qui décroche en premier fait l'intervention, "
                    "et celui qui rappelle une heure plus tard tombe sur un client déjà dépanné.",
            "calls": [
                "Une porte claquée, avec quelqu'un sur le palier qui appelle en marchant.",
                "Un cambriolage ou une tentative, à sécuriser dans la journée.",
                "Une clé cassée dans la serrure, souvent tard le soir.",
                "Un changement de cylindre après un déménagement, moins urgent mais plus rentable.",
            ],
            "captured": [
                "Le type de porte et de serrure, et s'il y a une carte de propriété.",
                "Si la personne est enfermée dehors, avec ou sans enfant ou animal à l'intérieur.",
                "L'adresse exacte et l'accès à l'immeuble.",
            ],
            "stake": "Sur une porte claquée, le client compose le numéro suivant au bout de deux "
                     "sonneries. Vous ne perdez pas un devis : vous perdez l'intervention du jour.",
            "faq": [
                (
                    "Comment éviter les appels hors zone la nuit ?",
                    "Vous renseignez votre zone d'intervention et votre rayon. Une demande qui tombe en "
                    "dehors reçoit une réponse polie et n'arrive pas dans votre liste — vous n'êtes "
                    "réveillé que pour ce que vous pouvez faire.",
                ),
                (
                    "L'assistant annonce-t-il un tarif ?",
                    "Non. Il note la demande et vous laisse annoncer votre prix, comme au téléphone. "
                    "Le tarif d'une ouverture dépend de la porte, et personne ne devrait s'engager à "
                    "votre place.",
                ),
            ],
        },
        "en": {
            "h1": "Phone answering service for locksmiths",
            "lead": "A slammed door does not wait. Whoever picks up first does the job; whoever calls "
                    "back an hour later reaches a customer already let in.",
            "calls": [
                "A slammed door, from someone on the landing calling as they walk.",
                "A break-in or an attempt, to be secured the same day.",
                "A key snapped in the lock, often late in the evening.",
                "A cylinder change after a move — less urgent, better paid.",
            ],
            "captured": [
                "The type of door and lock, and whether there is a security card.",
                "Whether they are locked out, and whether a child or a pet is inside.",
                "The exact address and how to get into the building.",
            ],
            "stake": "On a lockout the caller dials the next number after two rings. You are not losing "
                     "a quote — you are losing today's job.",
            "faq": [
                (
                    "How do I avoid out-of-area calls at night?",
                    "You set your service area and radius. A request outside it gets a polite answer and "
                    "never reaches your list — you are only woken for work you can actually take.",
                ),
                (
                    "Does the assistant quote a price?",
                    "No. It records the request and leaves the price to you, exactly as on the phone. "
                    "What an opening costs depends on the door, and nobody should commit on your behalf.",
                ),
            ],
        },
    },
    "electricien": {
        "fr": {
            "h1": "Secrétariat téléphonique pour électricien",
            "lead": "Vous êtes dans un tableau, courant coupé, gants aux mains. Un appel manqué à ce "
                    "moment-là, c'est une panne que quelqu'un d'autre ira voir cet après-midi.",
            "calls": [
                "Une panne totale ou un disjoncteur qui refuse de se réarmer.",
                "Une odeur de brûlé ou une prise qui chauffe, à voir tout de suite.",
                "Un tableau à mettre aux normes avant une vente ou une location.",
                "Une demande de devis de rénovation, souvent comparée à deux autres.",
            ],
            "captured": [
                "Ce qui ne fonctionne plus : tout le logement, un circuit, une pièce.",
                "S'il y a une odeur, de la fumée ou un point chaud — ce qui change la priorité.",
                "L'âge du tableau et le type de logement.",
            ],
            "stake": "Une panne totale se règle dans la journée ou elle change d'électricien. Et c'est "
                     "souvent la panne qui amène la mise aux normes derrière.",
            "faq": [
                (
                    "Un client peut-il décrire une panne électrique au téléphone ?",
                    "Il ne saura pas nommer un différentiel, mais il saura dire ce qui ne s'allume plus, "
                    "s'il sent quelque chose et si ça a déjà sauté. C'est ce qui est demandé, et c'est "
                    "ce qui vous permet de trier avant de rappeler.",
                ),
                (
                    "Et les demandes de devis qui ne sont pas urgentes ?",
                    "Elles sont notées comme telles et attendent votre rappel, sans se mélanger aux "
                    "pannes. Vous traitez les urgences d'abord et les devis le soir.",
                ),
            ],
        },
        "en": {
            "h1": "Phone answering service for electricians",
            "lead": "You are inside a consumer unit, power off, gloves on. A call missed right then is a "
                    "fault somebody else looks at this afternoon.",
            "calls": [
                "A total outage, or a breaker that will not reset.",
                "A burning smell or a hot socket, to be seen immediately.",
                "A board to bring up to code before a sale or a letting.",
                "A renovation quote, usually compared against two others.",
            ],
            "captured": [
                "What stopped working: the whole property, one circuit, one room.",
                "Whether there is a smell, smoke or a hot spot — which changes the priority.",
                "The age of the board and the type of property.",
            ],
            "stake": "A total outage is fixed the same day or it changes electrician. And it is usually "
                     "the fault that brings the rewiring behind it.",
            "faq": [
                (
                    "Can a customer describe an electrical fault over the phone?",
                    "They will not name an RCD, but they can say what stopped working, whether they can "
                    "smell anything and whether it has tripped before. That is what gets asked, and it "
                    "is what lets you triage before calling back.",
                ),
                (
                    "What about quotes that are not urgent?",
                    "They are recorded as such and wait for your call, without mixing into the faults. "
                    "You handle emergencies first and quotes in the evening.",
                ),
            ],
        },
    },
    "chauffagiste": {
        "fr": {
            "h1": "Secrétariat téléphonique pour chauffagiste",
            "lead": "Au premier coup de froid, votre téléphone sonne toute la journée pendant que vous "
                    "êtes déjà en intervention. C'est la semaine où l'on gagne — ou perd — l'année.",
            "calls": [
                "Une chaudière en panne, un matin d'octobre, avec des enfants dans le logement.",
                "Un radiateur froid ou un circuit qui ne monte plus en température.",
                "Un entretien annuel obligatoire, à caler avant l'hiver.",
                "Un remplacement de chaudière, avec des questions d'aides et de délais.",
            ],
            "captured": [
                "La marque et le modèle de la chaudière, et le code d'erreur affiché.",
                "S'il reste de l'eau chaude, et si le logement est encore chauffé.",
                "Si un contrat d'entretien est en cours chez vous.",
            ],
            "stake": "En pleine saison, un appel manqué n'est pas un dépannage perdu : c'est un contrat "
                     "d'entretien qui part chez un autre pour les dix ans qui suivent.",
            "faq": [
                (
                    "Comment gérer le pic d'appels des premiers froids ?",
                    "La ligne décroche autant de fois qu'il le faut, en même temps s'il le faut. Vous "
                    "retrouvez la file dans votre espace, triée par urgence, au lieu d'une liste "
                    "d'appels manqués sans nom.",
                ),
                (
                    "L'assistant distingue-t-il un client sous contrat ?",
                    "Il demande si un contrat d'entretien est en cours et le note avec la demande. À "
                    "vous de décider ce qui passe devant — mais vous le décidez en le sachant.",
                ),
            ],
        },
        "en": {
            "h1": "Phone answering service for heating engineers",
            "lead": "At the first cold snap your phone rings all day while you are already on a job. It "
                    "is the week that makes — or loses — the year.",
            "calls": [
                "A dead boiler on an October morning, with children in the house.",
                "A cold radiator, or a circuit that will not come up to temperature.",
                "A mandatory annual service, to be booked before winter.",
                "A boiler replacement, with questions about grants and lead times.",
            ],
            "captured": [
                "The make and model of the boiler, and the error code on the display.",
                "Whether there is still hot water, and whether the property is still heated.",
                "Whether they already hold a service contract with you.",
            ],
            "stake": "In peak season a missed call is not a lost repair: it is a service contract going "
                     "to somebody else for the next ten years.",
            "faq": [
                (
                    "How do I handle the cold-snap spike?",
                    "The line answers as many times as it takes, simultaneously if it has to. You get the "
                    "queue in your workspace, sorted by urgency, instead of a list of nameless missed "
                    "calls.",
                ),
                (
                    "Does the assistant know a contract customer?",
                    "It asks whether a service contract is in place and records it with the request. What "
                    "goes first is your call — but you make it knowing.",
                ),
            ],
        },
    },
    "vitrier": {
        "fr": {
            "h1": "Secrétariat téléphonique pour vitrier",
            "lead": "Une vitrine cassée ne peut pas rester ouverte jusqu'à demain. Ces appels-là se "
                    "gagnent dans l'heure, et souvent pendant que vous êtes déjà sur un chantier.",
            "calls": [
                "Une vitrine ou une baie brisée, à sécuriser en urgence.",
                "Un double vitrage embué ou fissuré, moins urgent, à mesurer.",
                "Une effraction, avec un constat et une assurance derrière.",
                "Un remplacement après grêle ou tempête, en série sur quelques jours.",
            ],
            "captured": [
                "Les dimensions approximatives et le type de vitrage.",
                "Si l'ouverture est sécurisée ou si le local est resté accessible.",
                "S'il y a un sinistre déclaré et un numéro de dossier d'assurance.",
            ],
            "stake": "Une vitrine cassée trouve toujours quelqu'un dans l'heure. La seule question est "
                     "de savoir si c'est vous.",
            "faq": [
                (
                    "Peut-on obtenir les dimensions par téléphone ?",
                    "Un ordre de grandeur, oui, et le type de vitrage. C'est ce qui vous permet de partir "
                    "avec le bon matériel ou d'annoncer un délai juste au lieu de vous déplacer pour "
                    "mesurer.",
                ),
                (
                    "Et les appels d'assurance ou de gestionnaire ?",
                    "Le numéro de sinistre et l'interlocuteur sont notés avec la demande, pour que le "
                    "dossier ne se reconstruise pas de mémoire trois jours plus tard.",
                ),
            ],
        },
        "en": {
            "h1": "Phone answering service for glaziers",
            "lead": "A broken shopfront cannot stay open until tomorrow. Those calls are won within the "
                    "hour, usually while you are already on a job.",
            "calls": [
                "A broken shopfront or picture window, to be made safe urgently.",
                "A misted or cracked double-glazed unit — less urgent, needs measuring.",
                "A break-in, with a report and an insurer behind it.",
                "Replacements after hail or a storm, in runs over a few days.",
            ],
            "captured": [
                "Rough dimensions and the type of glazing.",
                "Whether the opening is secured or the premises are still exposed.",
                "Whether a claim has been filed, and its reference.",
            ],
            "stake": "A broken shopfront always finds somebody within the hour. The only question is "
                     "whether it is you.",
            "faq": [
                (
                    "Can dimensions be taken over the phone?",
                    "A rough size, yes, and the type of glazing. That is what lets you turn up with the "
                    "right stock, or give an honest lead time instead of driving out to measure.",
                ),
                (
                    "What about insurer and property-manager calls?",
                    "The claim reference and the contact are recorded with the request, so the file is "
                    "not rebuilt from memory three days later.",
                ),
            ],
        },
    },
}


def has_page(trade_key: str | None) -> bool:
    return trade_key in INTENT_TRADES


def content_for(trade_key: str, lang: str = "fr") -> dict | None:
    """The trade's payload in ``lang``, falling back to French.

    French is the fallback rather than English on purpose: the search intent
    these pages answer (« secrétariat téléphonique plombier ») is French, and a
    half-translated page is worse than one that reads well.
    """
    entry = CONTENT.get(trade_key)
    if not entry:
        return None
    return entry.get(lang) or entry["fr"]


def other_trades(current: str | None = None) -> list[str]:
    return [t for t in INTENT_TRADES if t != current]
