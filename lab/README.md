# Pocket Deck Lab

Fork de [deckgym-core](https://github.com/bcollazo/deckgym-core) (moteur Rust de Pokémon TCG Pocket, AGPL-3.0).
Objectif : un simulateur assez fidèle pour construire des decks automatiquement — decks 100 % IA contre le méta,
contre des decks aléatoires, ou construits autour d'une ou plusieurs cartes imposées.

## État (9 octobre 2026)

- **Cartes** : 4 317 / 4 317 implémentées. Les 46 dresseurs réimprimés du Deluxe Pack Mega (B4b) sont branchés
  sur la logique de leur impression d'origine (`lab/tools/wire_reprints.py`).
- **Tests** : 1 190 passent, 0 échec (`cargo test --release --features "tui test-utils"`).
- **Fidélité** : mesurée sur 10 matchups réels (Limitless, format B4a) entre 5 decks du méta.

| Bot | Écart moyen (RMSE) vs réel | Vitesse (2 cœurs) |
|---|---|---|
| Expectiminimax profondeur 2 (`e2`) | 19,7 points | ~12 parties/s |
| Expectiminimax profondeur 3 (`e3`) | 11,8 points | ~3,7 parties/s |
| MCTS (`m`) | non mesuré | ~0,08 partie/s |

Le goulot d'étranglement est la qualité du bot, pas les règles.

## Structure

```
lab/
  decks/meta/      listes réelles de tournoi (Limitless) : butterfree, lucario, altaria, dedenne, hoopa
  decks/           decks expérimentaux
  tools/validate.py       compare le simulateur aux vrais matchups : python3 lab/tools/validate.py e3 100
  tools/wire_reprints.py  rebranche les réimpressions de dresseurs non implémentées
```

## Feuille de route

1. **Bot** : descendre sous ~5 points d'écart avec les vrais matchups (fonction de valeur, profondeur, audit de
   l'information cachée). Point faible connu : le verrou de sommeil d'Altaria (Lucario vs Altaria : +28 points).
2. **Méta** : vraies listes des 16 archétypes principaux, pondérées par leur part de tournoi.
3. **Recherche** : algorithme génétique sur toutes les cartes légales, avec contraintes optionnelles
   (« construis autour de cette carte »), test de robustesse, top 3 expliqué.
4. **Boucle réelle** : les parties jouées en vrai servent à recalibrer.

## Notes

- Pièces : aucune preuve datamine ou officielle d'un biais ; le moteur suppose 50/50.
- Premier deck testé (Fort-Ivoire / Méga-Lockpin, issu d'un prototype Python) : ~50 % en moyenne avec les règles
  exactes et `e3`, loin des 77 % annoncés par le prototype. Les résultats viendront désormais de ce moteur.
