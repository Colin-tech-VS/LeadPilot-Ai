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

## 2 bis. Routage public — la boucle `TooManyRedirects`

> **À lire avant de « corriger » une boucle de redirection.** Six commits
> successifs ont modifié `apache.conf`, `apache/pilotcore.conf`, `nginx.conf` et
> `.htaccess` puis ont été rollbackés parce que « le site est toujours DOWN ».
> Ces quatre fichiers **ne sont déployés nulle part** : le dépôt part sur
> Scalingo via le buildpack Python (`Procfile` → gunicorn), qui n'exécute ni
> Apache ni nginx. Les éditer ne change rien à la production.

### Topologie réelle

| Élément | État mesuré |
|---------|-------------|
| DNS `pilotcore.fr` et `www.pilotcore.fr` | pointent tous deux sur **LWS** (`2a00:7ee0:8:0:3:3884:0:d6a`) |
| Edge qui répond | `Apache/2.4.68 (Debian)` de LWS — réponses `charset=iso-8859-1` |
| App Flask | Scalingo `leadpilot-ai` (région `osc-fr1`) |
| `pilotcore.fr` côté Scalingo | domaine ajouté, **`Pending DNS`** (le DNS ne pointe pas vers Scalingo) |
| `www.pilotcore.fr` côté Scalingo | domaine **canonique**, certificat Let's Encrypt créé |

### Les deux boucles, mesurées

1. **LWS renvoie l'hôte vers lui-même.** Sur *tous* les chemins :

   ```
   GET https://www.pilotcore.fr/  ->  301  Location: https://www.pilotcore.fr/
   ```

   La cible est identique à la requête : le navigateur boucle jusqu'à
   `TooManyRedirects`. La requête **n'atteint jamais Scalingo**. C'est la panne
   principale, et elle se règle **dans le panel LWS uniquement**.

2. **Le domaine canonique Scalingo renvoie vers cet hôte mort.** Le routeur
   Scalingo 301 tout ce qui n'est pas `www.pilotcore.fr` vers
   `https://www.pilotcore.fr` — y compris `/api/health` et les `POST` de
   webhooks, que Flask n'aurait jamais redirigés :

   ```
   POST https://leadpilot-ai.osc-fr1.scalingo.io/webhook/inbound-call
     ->  301  Location: https://www.pilotcore.fr/webhook/inbound-call
   ```

   L'app est donc injoignable même par son URL Scalingo.

### Ce que Flask fait (et ne fait pas)

`app/core/canonical.py` **ne redirige jamais** entre `www` et l'apex : les deux
hôtes sont servis en 200, seul `http` → `https` (même hôte) et l'IPv4 publique
sont réécrits. Les sondes (`/health`, `/health/ready`, `/api/health`, `/api`) et
les méthodes mutantes ne sont jamais redirigées. `tests/test_canonical_host.py`
verrouille ce comportement : **la boucle ne vient pas de l'application**.

### Remise en service

Dans cet ordre — les étapes 1 et 2 demandent les accès LWS, hors du dépôt :

1. **Panel LWS** — supprimer la redirection du domaine. Une entrée de type
   « redirection » vers `https://www.pilotcore.fr` (ou un `force-www` appliqué
   à un vhost qui sert déjà `www`) redirige l'hôte vers lui-même. Vérifier
   aussi un `.htaccess` résiduel à la racine du docroot LWS.
2. **DNS** — faire pointer le site vers Scalingo :
   - `www.pilotcore.fr` → `CNAME` vers `leadpilot-ai.osc-fr1.scalingo.io.`
   - `pilotcore.fr` (apex) → la cible apex indiquée par Scalingo (le domaine
     reste `Pending DNS` tant que ce n'est pas fait).
3. **Scalingo — ✅ fait le 3 septembre 2026.** Le domaine canonique a été
   retiré : il renvoyait l'app vers l'hôte en panne.

   ```bash
   scalingo --app leadpilot-ai --region osc-fr1 unset-canonical-domain
   scalingo --app leadpilot-ai --region osc-fr1 domains   # plus de (*)
   ```

   Effet mesuré immédiatement après — l'app est de nouveau joignable par son
   URL Scalingo, sans redirection :

   | Chemin | Avant | Après |
   |--------|-------|-------|
   | `GET /api/health` | `301` → `www.pilotcore.fr` | `200 {"status":"ok"}` |
   | `GET /` | `301` | `200` |
   | `GET /admin` | `301` | `302` vers `/admin/login` (même hôte) |
   | `POST /webhook/inbound-call` | `301` | `401` (signature vérifiée) |

   Les webhooks Twilio et Stripe repassent donc par l'app au lieu d'être
   redirigés en `301` — un `POST` redirigé perd son corps.

   Ne **pas** remettre `set-canonical-domain` tant que le DNS de `www` ne
   pointe pas sur Scalingo : le routeur renverrait de nouveau vers LWS.

### Vérification

```bash
python3 scripts/check_public_endpoints.py
```

La sonde suit chaque chaîne de redirection et échoue sur un saut vers soi-même,
un cycle, une chaîne sans fin ou une `Location` RFC1918. Elle tourne à chaque
deploy (`deploy-scalingo.yml`). L'ancienne version, qui acceptait tout 3xx dont
la `Location` n'était pas privée, affichait le vert sur le site en panne.

## 3. Sécurité

- `TWILIO_AUTO_PROVISION_NUMBERS` : un numéro dédié n’est acheté **qu’après paiement Stripe**, et seulement si `LIVE_PROVIDER_SPEND=1`.
- `LIVE_PROVIDER_SPEND=1` en production. En local / pytest c’est forcément off (pas de SMS, pas d’achat de numéro, pas de Places/OpenAI facturés).
- `TWILIO_VALIDATE_SIGNATURE=1`.
- Pas de hash admin par défaut en prod — uniquement `ADMIN_PASSWORD`.

## 4. Réseaux sociaux (admin `/admin/social`)

Redirect URIs à autoriser **à l'identique** (prod + local) :

| Réseau | Variables Scalingo | Redirect URI |
|--------|--------------------|--------------|
| Facebook | `FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET` | `https://www.pilotcore.fr/admin/social/facebook/callback` |
| LinkedIn | `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | `https://www.pilotcore.fr/admin/social/linkedin/callback` |

LinkedIn : créer une app sur [LinkedIn Developers](https://www.linkedin.com/developers/apps). Produits ouverts ici : **Sign In with LinkedIn** + **Share on LinkedIn** (+ Ad Library). **Community Management API** n'est pas demandable — les posts partent donc sur le **profil du compte qui autorise**, pas au nom de la page. Scopes OAuth self-serve : `profile`, `email`, `w_member_social` (ceux listés dans l'onglet Auth). Ne pas demander `openid` sauf si « Sign In with LinkedIn using OpenID Connect » est bien ajouté — sinon LinkedIn répond « The requested permission scope is not valid ». Override : `LINKEDIN_OAUTH_SCOPES`. Demander `w_organization_social` sans le produit fait échouer OAuth (« Bummer, something went wrong ») et LinkedIn renvoie vers l'URL du site au lieu du callback.

Redirect URI à coller **à l'identique** dans Auth → Authorized redirect URLs : `https://www.pilotcore.fr/admin/social/linkedin/callback` (pas seulement `www.pilotcore.fr`).

Lien de vérification page (super admin) : [https://www.pilotcore.fr/verification-linkedin](https://www.pilotcore.fr/verification-linkedin) — URL LinkedIn `https://www.linkedin.com/developers/apps/verification/a7910099-0f14-415c-b7a4-9850c46a4380`.

L'autopublication (cron `scripts/publish_queued_social.py`, toutes les 15 min) envoie le même aperçu sur **chaque** réseau connecté.

## 5. Twilio

Numéro : **+33 1 59 16 96 91** (`+33159169691`)

Configurer dans la console Twilio → numéro → **A CALL COMES IN** :

```
https://PilotCore-ai.osc-fr1.scalingo.io/voice/inbound
```

Méthode : `POST`

## 6. Stripe

1. Créer les produits Starter (149 €), Pro (349 €), Premium (699 €).
2. **Activer Stripe Connect** (Express) dans le Dashboard Stripe — les acomptes carte client sont versés sur le compte Stripe de l'artisan.
3. Webhook endpoint :

```
https://PilotCore-ai.osc-fr1.scalingo.io/billing/webhook
```

Événements : `checkout.session.completed`, `customer.subscription.*`, `account.updated`  
Copier `STRIPE_WEBHOOK_SECRET` dans Scalingo.

Optionnel : `STRIPE_CONNECT_FEE_PERCENT` (0 par défaut) pour une commission plateforme sur les acomptes carte.

## 7. Health checks

| Probe | URL |
|-------|-----|
| Liveness | `GET /health` |
| Readiness (BDD) | `GET /health/ready` |

Configurer Scalingo health check sur `/health/ready`.

## 8. Rate limits (actifs)

| Route | Limite |
|-------|--------|
| `POST /register` | 5 / heure / IP |
| `POST /auth/register` | 5 / heure / IP |
| `POST /demo/simulate` | 15 / min / IP |
| `POST /chat/<id>/message` | 30 / min / IP |
| Login web / API / admin | déjà en place |

## 9. Tests avant deploy

```bash
pytest -q
```

Ou push sur `main` → CI GitHub.

## 10. GitHub secrets

- `SCALINGO_API_TOKEN` pour le workflow de deploy.
