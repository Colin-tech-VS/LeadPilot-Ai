"""Bulk-import artisan prospects with a real e-mail from the ADEME RGE register.

Usage:
    python scripts/source_artisans.py [--target 200] [--trades plombier,couvreur]
                                      [--departments 69,33] [--dry-run]

Run it once to fill the prospect base, then build a campaign in /admin/campagnes.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.services import artisan_sourcing  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import d'artisans depuis le registre RGE (ADEME).")
    parser.add_argument("--target", type=int, default=200, help="Nombre d'artisans à importer (défaut 200).")
    parser.add_argument("--trades", default="", help="Métiers séparés par des virgules (défaut : tous).")
    parser.add_argument("--departments", default="", help="Départements séparés par des virgules.")
    parser.add_argument("--dry-run", action="store_true", help="Compter les lignes éligibles sans rien écrire.")
    args = parser.parse_args()

    trades = [t.strip() for t in args.trades.split(",") if t.strip()] or None
    departments = [d.strip() for d in args.departments.split(",") if d.strip()] or None

    app = create_app()
    with app.app_context():
        try:
            if args.dry_run:
                total = artisan_sourcing.preview_available(trades=trades, departments=departments)
                print(f"{total} lignes éligibles dans le registre (avant dédoublonnage).")
                return 0

            result = artisan_sourcing.source_artisans(
                target=args.target, trades=trades, departments=departments
            )
        except artisan_sourcing.SourcingError as exc:
            print(f"Échec : {exc}", file=sys.stderr)
            return 1

    print(f"{result['imported']} artisans importés (objectif {result['target']}).")
    print(f"  Lignes analysées   : {result['scanned']}")
    print(f"  Doublons écartés   : {result['skipped']['duplicate']}")
    print(f"  E-mails invalides  : {result['skipped']['invalid_email']}")
    print(f"  Hors métier ciblé  : {result['skipped']['off_trade']}")
    for trade, count in sorted(result["by_trade"].items(), key=lambda kv: -kv[1]):
        print(f"  · {trade:<14} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
