# Wikidéb’IA Quote Resolver — v0.5 experimental

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

## Nouvelle expérimentation v0.5

Les archives MIA et Wikirouge sont considérées comme une même famille documentaire par prudence. La comparaison groupe les passages textuels identiques après normalisation, avec conservation des références de chaque fournisseur ; elle ne présume pas que toutes les éditions sont identiques. Les statistiques portent sur quatre citations, et non huit preuves indépendantes.

Pour les citations françaises non localisées exactement, la recherche par distance d'édition explore des sous-chaînes autour du sélecteur, même au sein d'un long paragraphe. Les résultats restent des variantes à vérifier, jamais des correspondances exactes ni des autorisations de publication. La portée est bornée et le sélecteur doit être unique dans la citation.

Le mode `python pilot_full.py --provider preferred` privilégie MIA : si la source française est unique, l'inventaire complet et un candidat d'alignement par phrases disponible, il ne parcourt pas aussi Wikirouge pour cette citation. Sinon, il tente le second parcours. Cette règle optimise la **recherche de candidats** et ne prouve pas leur fidélité. Le banc comparatif complet reste le mode par défaut. La CI mesure aussi le parcours préférentiel à partir du cache du test complet, sans nouvelle requête ; ses chiffres de requêtes potentielles ne sont pas des durées réseau mesurées. Les URL MIA et titres Wikirouge des deux œuvres restent pré-identifiés ; la découverte générale d'œuvres et de traductions reste à développer.

## Navigation bibliographique (expérience séparée)

Le modèle Text de Wikirouge comporte une propriété sémantique MIA URL dans les deux langues ; le wiki anglais centralise les interlangues. Documentation : https://wikirouge.net/texts/fr/Aide:Accueil/Architecture_du_site et https://wikirouge.net/texts/en/Template:Text.

Le module `navigation.py` extrait les métadonnées des modèles Citation originaux, tente la recherche inverse de l'URL MIA via Semantic MediaWiki, puis à défaut une correspondance de titre sur le wiki français. Il vérifie les interlangues HTML réciproques et lit les métadonnées brutes des deux œuvres, dont MIA URL ; l'API ask par titre sert de repli. Une URL anglaise trouvée est vérifiée en lecture HTTP. Aucune URL cible du benchmark n'est utilisée pour choisir un résultat. Les réponses restent des pistes bibliographiques, non des citations approuvées. Les métadonnées absentes, ambiguës et les erreurs sont explicitement signalées.

```sh
python -m pip install -r requirements-navigation.txt
python navigation.py --replay
```

Le job navigation teste les neuf cas du corpus, y compris les cas non confirmés, et conserve uniquement les rapports de métadonnées et d'empreintes. Les extraits de citation ne figurent ni dans les rapports ni dans les logs. Ce parcours ne télécharge pas les œuvres intégrales et ne suppose pas que le champ MIA URL est rempli sur toutes les pages. La vérification des passages et des droits reste distincte.

## Première recherche automatique de passages (sans URL cible pré-identifiée)

Le pilote `auto_retrieve.py --replay` utilise le même cache que navigation.py et le wikicode de la Citation comme entrée. Les noms génériques d'archives (notamment Marxists.org) sont exclus comme titres d'œuvres ; deux racines réciproques doivent comporter le modèle Text. La vérification de l'auteur exploite également le tableau de métadonnées rendu, lorsque le paramètre brut n'est pas exploitable.

Le moteur examine d'abord l'URL française citée, puis les liens directs du sommaire MIA et, si nécessaire, les sous-pages Wikirouge. Les règles de collecte MIA sont bornées (liens du même répertoire pour un index, même radical pour les anciens fichiers, préfaces datées explicitement liées). Elles ne démontrent pas une couverture intégrale. Le moteur ne recherche l'anglais qu'après avoir retrouvé une source française exacte dans les documents examinés ou une variante à revoir. Il privilégie l'URL MIA anglaise explicitement fournie par Wikirouge, et se replie sur les sous-pages anglaises de Wikirouge. Les rangs sont évalués après recherche ; pas de correspondances de chapitres ni d'URL cible présélectionnées. Ce pilote n'effectue pas encore d'alignement structurel automatique. Il ne valide ni les limites, ni les droits, ni la fidélité d'une citation.

## Avant intégration au dépôt principal

Confirmer les résultats sur les sources réelles, élargir le corpus indépendant et les cas négatifs, évaluer les droits et les limites des citations, exploiter les métadonnées des modèles Citation bruts, mesurer le coût réel. Ne pas relâcher les critères pour obtenir une CI verte. L'intégration au workflow de traduction fera l'objet d'une décision séparée.
