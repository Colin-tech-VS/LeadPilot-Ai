#!/usr/bin/env bash
#
# Configure toutes les variables d'environnement LeadPilot AI sur Scalingo en
# une seule commande (companion portable de deploy_scalingo.ps1 pour Linux/Mac).
#
# Il NE déploie pas et NE dépense rien : il pousse seulement la config. Les
# valeurs sont lues depuis un fichier .env local (par défaut ./.env) ; toute
# variable absente est simplement ignorée.
#
# Prérequis :
#   - Scalingo CLI installée : https://cli.scalingo.com
#   - Connecté : `scalingo login` (ou export SCALINGO_API_TOKEN=...)
#
# Usage :
#   scripts/setup_scalingo.sh                 # app leadpilot-ai, lit ./.env
#   scripts/setup_scalingo.sh -a mon-app -e prod.env
#   scripts/setup_scalingo.sh --dry-run       # affiche sans rien pousser
set -euo pipefail

APP="leadpilot-ai"
ENV_FILE=".env"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -a|--app) APP="$2"; shift 2 ;;
    -e|--env-file) ENV_FILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Argument inconnu : $1" >&2; exit 1 ;;
  esac
done

REGION="${SCALINGO_REGION:-osc-fr1}"
SERVER_NAME_DEFAULT="${APP}.${REGION}.scalingo.io"

# Variables poussées si présentes dans l'environnement ou le fichier .env.
KEYS=(
  FLASK_ENV PREFERRED_URL_SCHEME SERVER_NAME SECRET_KEY JWT_SECRET_KEY DATABASE_URL
  MISTRAL_API_KEY MISTRAL_MODEL OPENAI_API_KEY
  TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_DEFAULT_TENANT_ID
  TWILIO_AI_PHONE_NUMBER TWILIO_AI_PHONE_DISPLAY TWILIO_SMS_FROM
  TWILIO_AUTO_PROVISION_NUMBERS TWILIO_NUMBER_COUNTRY TWILIO_NUMBER_AREA_CODE
  TWILIO_VOICE TWILIO_SPEECH_MODEL TWILIO_VALIDATE_SIGNATURE
  STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET
  ADMIN_USERNAME ADMIN_PASSWORD EMAIL_FROM WEBHOOK_SECRET
)

# Charge le fichier .env (sans écraser une variable déjà exportée dans le shell).
declare -A VALUES
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
      k="${BASH_REMATCH[1]}"; v="${BASH_REMATCH[2]}"
      v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"   # trim quotes
      VALUES["$k"]="$v"
    fi
  done < "$ENV_FILE"
fi

# Défauts sûrs si non fournis.
: "${VALUES[FLASK_ENV]:=${FLASK_ENV:-production}}"
: "${VALUES[PREFERRED_URL_SCHEME]:=${PREFERRED_URL_SCHEME:-https}}"
: "${VALUES[SERVER_NAME]:=${SERVER_NAME:-$SERVER_NAME_DEFAULT}}"
: "${VALUES[TWILIO_AUTO_PROVISION_NUMBERS]:=${TWILIO_AUTO_PROVISION_NUMBERS:-1}}"
: "${VALUES[TWILIO_NUMBER_COUNTRY]:=${TWILIO_NUMBER_COUNTRY:-FR}}"

echo "App        : $APP ($REGION)"
echo "Fichier env: $ENV_FILE"
echo "SERVER_NAME: ${VALUES[SERVER_NAME]}"
echo

pushed=0
for key in "${KEYS[@]}"; do
  # priorité : variable shell exportée > fichier .env
  val="${!key:-${VALUES[$key]:-}}"
  [[ -z "$val" ]] && continue
  if [[ "$DRY_RUN" == "1" ]]; then
    # masque les secrets à l'affichage
    if [[ "$key" == *TOKEN* || "$key" == *SECRET* || "$key" == *PASSWORD* || "$key" == *AUTH* ]]; then
      echo "  [dry-run] env-set $key=********"
    else
      echo "  [dry-run] env-set $key=$val"
    fi
  else
    echo "  env-set $key"
    scalingo --app "$APP" env-set "$key=$val" >/dev/null
  fi
  pushed=$((pushed + 1))
done

echo
if [[ "$DRY_RUN" == "1" ]]; then
  echo "Dry-run terminé — $pushed variable(s) seraient poussées. Aucune modification."
else
  echo "Terminé — $pushed variable(s) configurées sur $APP."
  echo "Webhook voix Twilio auto-configuré à l'inscription : https://${VALUES[SERVER_NAME]}/voice/inbound"
fi
