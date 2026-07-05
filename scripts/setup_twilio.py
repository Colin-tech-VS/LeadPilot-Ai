"""Assistant de configuration Twilio pour les numéros IA multi-tenant.

Ce que le script FAIT (avec TES clés, en une commande) :
  1. vérifie que tes identifiants Twilio fonctionnent ;
  2. interroge l'API en direct pour afficher EXACTEMENT ce que le bundle
     réglementaire France exige (champs entreprise + justificatifs) ;
  3. avec --create, crée l'ossature : « end user » entreprise + adresse +
     bundle réglementaire, et t'affiche le lien console pour finir.

Ce que le script NE FAIT PAS (et ne peut pas) :
  - il ne soumet pas le dossier à la validation : l'upload du Kbis et le clic
    final se font dans la console Twilio, car un dossier incomplet gâche un
    cycle de review (qui prend des heures/jours) ;
  - il ne contourne pas la validation humaine imposée par la réglementation FR.

Prérequis :
  pip install twilio
  export TWILIO_ACCOUNT_SID=AC...    export TWILIO_AUTH_TOKEN=...

Usage :
  python scripts/setup_twilio.py                     # inspecte (lecture seule)
  python scripts/setup_twilio.py --create \
      --business "LeadPilot SAS" --email you@leadpilot.ai \
      --street "10 rue X" --city Paris --postal 75001
  python scripts/setup_twilio.py --country US        # tester sans contrainte FR
"""
import argparse
import os
import sys

CONSOLE_BUNDLES = "https://console.twilio.com/us1/develop/phone-numbers/regulatory-compliance/bundles"


def _client():
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        sys.exit("✗ TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN manquants dans l'environnement.")
    try:
        from twilio.rest import Client
    except ImportError:
        sys.exit("✗ SDK manquant : pip install twilio")
    return Client(sid, token)


def _check_credentials(client) -> None:
    try:
        acct = client.api.accounts(client.username).fetch()
        print(f"✓ Connecté à Twilio : {acct.friendly_name} ({acct.status})")
    except Exception as exc:
        sys.exit(f"✗ Identifiants Twilio invalides : {exc}")


def _show_requirements(client, country: str, number_type: str) -> None:
    print(f"\n— Exigences réglementaires pour {country}/{number_type}/business —")
    try:
        regs = client.numbers.v2.regulatory_compliance.regulations.list(
            iso_country=country, number_type=number_type, end_user_type="business", limit=5
        )
    except Exception as exc:
        print(f"  (impossible de lire les régulations : {exc})")
        return
    if not regs:
        print("  Aucune régulation renvoyée — ce pays/type n'a peut-être pas de contrainte.")
        return
    for reg in regs:
        print(f"  • {reg.friendly_name} (sid={reg.sid})")
        reqs = reg.requirements or {}
        for kind in ("end_user", "supporting_document"):
            for item in reqs.get(kind, []) or []:
                name = item.get("name") if isinstance(item, dict) else item
                print(f"      - {kind}: {name}")


def _show_number_availability(client, country: str) -> None:
    print(f"\n— Numéros voix disponibles à l'achat en {country} —")
    for kind in ("local", "mobile"):
        try:
            catalog = getattr(client.available_phone_numbers(country), kind)
            found = catalog.list(voice_enabled=True, limit=1)
            if found:
                print(f"  ✓ {kind}: ex. {found[0].phone_number}")
            else:
                print(f"  – {kind}: aucun disponible")
        except Exception as exc:
            print(f"  – {kind}: {exc}")


def _create_scaffold(client, args) -> None:
    print("\n— Création de l'ossature du bundle —")
    if not (args.business and args.email):
        sys.exit("✗ --create exige au minimum --business et --email.")

    # 1) End user entreprise
    try:
        end_user = client.numbers.v2.regulatory_compliance.end_users.create(
            friendly_name=args.business,
            type="business",
            attributes={"business_name": args.business},
        )
        print(f"  ✓ End user créé : {end_user.sid}")
    except Exception as exc:
        sys.exit(f"✗ Échec création end user : {exc}")

    # 2) Adresse (facultative mais utile pour le dossier)
    address_sid = None
    if args.street and args.city and args.postal:
        try:
            address = client.addresses.create(
                customer_name=args.business,
                street=args.street,
                city=args.city,
                region=args.city,
                postal_code=args.postal,
                iso_country=args.country,
            )
            address_sid = address.sid
            print(f"  ✓ Adresse créée : {address_sid}")
        except Exception as exc:
            print(f"  – Adresse non créée (à finir en console) : {exc}")

    # 3) Bundle
    try:
        bundle = client.numbers.v2.regulatory_compliance.bundles.create(
            friendly_name=f"LeadPilot AI — {args.business}",
            email=args.email,
            iso_country=args.country,
            number_type=args.number_type,
            end_user_type="business",
        )
        print(f"  ✓ Bundle créé : {bundle.sid} (statut: {bundle.status})")
    except Exception as exc:
        sys.exit(f"✗ Échec création bundle : {exc}")

    # 4) Rattache l'end user (et l'adresse) au bundle
    for sid, label in ((end_user.sid, "end user"), (address_sid, "adresse")):
        if not sid:
            continue
        try:
            client.numbers.v2.regulatory_compliance.bundles(bundle.sid).item_assignments.create(object_sid=sid)
            print(f"  ✓ {label} rattaché au bundle")
        except Exception as exc:
            print(f"  – {label} non rattaché ({exc})")

    print(
        "\n➡  Dernière étape MANUELLE (obligatoire, ~qq heures à 2 j de review) :\n"
        f"   1. Ouvre {CONSOLE_BUNDLES}/{bundle.sid}\n"
        "   2. Ajoute le justificatif (Kbis < 3 mois ou facture) en pièce jointe\n"
        "   3. Clique « Submit for review »\n"
        "   Une fois APPROVED, chaque numéro acheté par l'app s'y rattache automatiquement."
    )


def main(argv) -> None:
    p = argparse.ArgumentParser(description="Assistant de configuration Twilio LeadPilot AI.")
    p.add_argument("--country", default=os.environ.get("TWILIO_NUMBER_COUNTRY", "FR"))
    p.add_argument("--number-type", default="local", choices=["local", "mobile"])
    p.add_argument("--create", action="store_true", help="créer l'ossature du bundle")
    p.add_argument("--business", help="raison sociale (pour --create)")
    p.add_argument("--email", help="email de contact réglementaire (pour --create)")
    p.add_argument("--street")
    p.add_argument("--city")
    p.add_argument("--postal")
    args = p.parse_args(argv)
    args.country = args.country.upper()

    client = _client()
    _check_credentials(client)
    _show_requirements(client, args.country, args.number_type)
    _show_number_availability(client, args.country)
    if args.create:
        _create_scaffold(client, args)
    else:
        print("\nℹ  Lecture seule. Relance avec --create pour bâtir l'ossature du bundle.")


if __name__ == "__main__":
    main(sys.argv[1:])
