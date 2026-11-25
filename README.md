# Wikidéb’IA Quote Resolver — v0.4 experimental

Module autonome de recherche des traductions anglaises déjà publiées de citations françaises présentes dans Wikidébats. Ce dépôt expérimental ne modifie ni les wikis ni le dépôt principal. Aucun identifiant wiki n'est utilisé. Les candidats ne sont jamais publiés automatiquement.

## Tests sur GitHub Actions

Le workflow [pilot.yml](.github/workflows/pilot.yml) lance les tests unitaires, puis le pilote complet sur les sources publiques (Marxists.org, Wikirouge, Wikidébats) et un rejeu strict hors ligne. Il s'exécute lors de l'ouverture ou de la mise à jour d'une PR interne et peut être déclenché manuellement après fusion. Aucun lancement périodique. Le jeton GitHub est limité à la lecture des contenus ; checkout ne conserve pas d'identifiants. Durée maximale : 15 minutes.

Les rapports expurgés sont conservés sept jours sous l'artefact quote-pilot-reports : full-work.json, full-work-replay.json et ci-summary.json. Le résumé est également imprimé dans les logs. Aucun cache HTTP, poids de modèle, ni extrait de revue n'est téléversé. La CI reste en échec si le pilote est incomplet ou si les résultats du rejeu divergent. Un échec éditorial de localisation ne signifie pas nécessairement une panne de l'infrastructure.

## Exécution locale

Python 3.10+ ; dépendances requests et BeautifulSoup. Depuis la racine :

```sh
sh run-pilot.sh
```

Pour réutiliser le cache local du pilote 0.3, ajouter --cache suivi du chemin de l'ancien dossier .pilot-cache. Ne pas copier ou publier le cache ni les rapports non expurgés. Le rejeu strict 0.4 exige un rapport initial 0.4, non 0.3.

Pour une revue locale du cas de la préface de 1883, ajouter --review-case manifesto-preface-1883 ; vérifier les droits avant de partager les extraits. Ne pas utiliser cette option dans la CI publique.

Tests hors ligne : python -m unittest discover -s tests -v après installation des dépendances. Le test optionnel du cache vectoriel avec modèle simulé nécessite numpy. Les embeddings réels sont facultatifs : requirements-embeddings.txt, instantané local safetensors explicite, pas d'inférence distante.

## Périmètre et état

Le pilote utilise quatre citations réelles sur deux fournisseurs, soit huit recherches, dans le Manifeste du parti communiste et La Situation de la classe laborieuse en Angleterre. Les neuf cas recensés sont décrits dans cases-v02.json ; seules les quatre citations du pilote sont testées sur les œuvres complètes.

Le pilote 0.3 exécuté sur le serveur du projet avait récupéré les 55 pages MIA et 40 pages Wikirouge prévues (sommaires Wikirouge inclus), avec 108 requêtes. Le rejeu a utilisé 108 entrées en cache et aucune requête réseau. Les deux œuvres Wikirouge n'avaient pas pu être résolues par l'API native ; la préface de 1883 n'avait pas été localisée intégralement en français. La version 0.4 ajoute un parcours hybride (liens HTML réciproques puis inventaire API des sous-pages), la comparaison des résultats du rejeu et le diagnostic des variantes françaises.

La recherche lexicale et l'alignement par paragraphes ou phrases proposent des extraits candidats. L'évaluation utilise des ancres courtes après classement, jamais comme preuve de fidélité de la citation complète. Les références de sections sont fournies manuellement pour les deux œuvres du pilote. Aucun score n'est une probabilité de correspondance. Les droits, l'attribution et les limites exactes sont à vérifier par édition ; en cas de doute, s'abstenir. Les benchmarks synthétiques (benchmark_offline.py, benchmark_alignment.py) ne constituent pas une mesure indépendante de performance documentaire.

## Avant intégration au dépôt principal

Confirmer les résultats sur les sources réelles, élargir le corpus indépendant et les cas négatifs, évaluer les droits et les limites des citations, exploiter les métadonnées des modèles Citation bruts, mesurer le coût réel. Ne pas relâcher les critères pour obtenir une CI verte. L'intégration au workflow de traduction fera l'objet d'une décision séparée.
