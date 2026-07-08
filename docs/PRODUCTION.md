# Mise en production — PilotCore

Checklist dans l’ordre recommandé.

## 1. Supabase (EU)

1. Projet **eu-central-1** (ref `xtqhyvvsjoyscdclyaxp` dans `.env.example`).
2. Récupérer `DATABASE_URL` **pooler transaction mode, port 6543** (IPv4 — requis pour Scalingo).
3. Appliquer les migrations : au premier deploy Scalingo, la phase `release` exécute `alembic upgrade head`.

## 2. Scalingo

- App : `PilotCore-ai` (région `osc-fr1`).
- Script : `.\scripts\deploy_scalingo.ps1` (nécessite `SCALINGO_API_TOKEN`, `DATABASE_URL`, `ADMIN_PASSWORD`).
- CI GitHub : tests pytest puis deploy (`deploy-scalingo.yml`).

Variables **obligatoires** en production :

| Variable | Description |
|----------|-------------|
| `FLASK_ENV` | `production` |
| `SECRET_KEY` / `JWT_SECRET_KEY` | secrets aléatoires |
| `DATABASE_URL` | Supabase pooler :6543 |
| `PUBLIC_BASE_URL` | URL publique canonique (`https://www.pilotcore.fr`) — liens e-mail, webhooks |
| `ADMIN_PASSWORD` | console `/admin` |
| `WEBHOOK_SECRET` | header `X-Webhook-Secret` |
| `EMAIL_INBOUND_SECRET` | webhook email entrant |
| `MISTRAL_API_KEY` | extraction leads / chat |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | voix |
| `TWILIO_DEFAULT_TENANT_ID` | UUID tenant par défaut pour le numéro partagé |

### Email sortant (SMTP) — **obligatoire pour les e-mails transactionnels**

Sans ces variables, les envois (bienvenue, réinitialisation de mot de passe,
confirmation de RDV…) sont **simulés** et jamais réellement délivrés.

| Variable | Description |
|----------|-------------|
| `SMTP_HOST` | serveur SMTP (ex. `mail.pilotcore.fr`) |
| `SMTP_PORT` | `465` (SSL) ou `587` (STARTTLS) |
| `SMTP_USER` | boîte d'envoi (ex. `contact@pilotcore.fr`) |
| `SMTP_PASSWORD` | mot de passe de la boîte |
| `SMTP_USE_SSL` / `SMTP_USE_TLS` | `1`/`0` selon le port |
| `EMAIL_FROM` | adresse expéditeur affichée |

> **Vérification** : `/admin/diagnostics` liste l'état de chaque variable,
> teste la connexion SMTP en direct et envoie un e-mail de test.

#### Délivrabilité de la prospection — « Bounced by relay »

Un e-mail marqué **`sent`** côté app a seulement été *accepté par le relais
LWS* ; s'il rebondit ensuite, LWS l'affiche **`Bounced by relay`** (le serveur
du destinataire l'a refusé). Deux causes, dans l'ordre :

1. **Adresses invalides récoltées sur le web.** Le scraping capture parfois des
   références d'images rétina (`logo@2x.png`) ou des adresses mal formées qui
   ressemblent à `local@domaine.tld`. Elles rebondissent à 100 % et, en
   volume, dégradent la réputation d'envoi jusqu'à faire rebondir les e-mails
   *sains*. → Filtrées automatiquement à la récolte **et** avant chaque envoi
   (`app/services/email_validation.py`) : un prospect non délivrable passe en
   `skipped` sans jamais atteindre le relais.
2. **Authentification du domaine.** Pour que les serveurs destinataires
   acceptent `pilotcore.fr`, la zone DNS doit publier :
   - **SPF** — autoriser les serveurs d'envoi LWS (`v=spf1 include:_spf.lws.fr ~all`).
   - **DKIM** — activer la signature dans le panel LWS et publier la clé publique.
   - **DMARC** — `v=DMARC1; p=none; rua=mailto:contact@pilotcore.fr` pour démarrer.
   - **PTR** (reverse DNS) cohérent, géré par LWS.

   Sans SPF/DKIM/DMARC, la prospection à froid rebondit quel que soit le
   contenu — c'est un réglage **DNS**, pas applicatif.

## 3. Sécurité

- `TWILIO_AUTO_PROVISION_NUMBERS=0` au lancement (pas d’achat auto de numéros).
- `TWILIO_VALIDATE_SIGNATURE=1`.
- Pas de hash admin par défaut en prod — uniquement `ADMIN_PASSWORD`.

## 4. Twilio

Numéro : **+33 1 59 16 96 91** (`+33159169691`)

Configurer dans la console Twilio → numéro → **A CALL COMES IN** :

```
https://PilotCore-ai.osc-fr1.scalingo.io/voice/inbound
```

Méthode : `POST`

## 5. Stripe

1. Créer les produits Starter (149 €), Pro (349 €), Premium (699 €).
2. **Activer Stripe Connect** (Express) dans le Dashboard Stripe — les acomptes carte client sont versés sur le compte Stripe de l'artisan.
3. Webhook endpoint :

```
https://PilotCore-ai.osc-fr1.scalingo.io/billing/webhook
```

Événements : `checkout.session.completed`, `customer.subscription.*`, `account.updated`  
Copier `STRIPE_WEBHOOK_SECRET` dans Scalingo.

Optionnel : `STRIPE_CONNECT_FEE_PERCENT` (0 par défaut) pour une commission plateforme sur les acomptes carte.

## 6. Health checks

| Probe | URL |
|-------|-----|
| Liveness | `GET /health` |
| Readiness (BDD) | `GET /health/ready` |

Configurer Scalingo health check sur `/health/ready`.

## 7. Rate limits (actifs)

| Route | Limite |
|-------|--------|
| `POST /register` | 5 / heure / IP |
| `POST /auth/register` | 5 / heure / IP |
| `POST /demo/simulate` | 15 / min / IP |
| `POST /chat/<id>/message` | 30 / min / IP |
| Login web / API / admin | déjà en place |

## 8. Tests avant deploy

```bash
pytest -q
```

Ou push sur `main` → CI GitHub.

## 9. GitHub secrets

- `SCALINGO_API_TOKEN` pour le workflow de deploy.
