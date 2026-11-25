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

Le moteur examine d'abord l'URL française citée, puis les liens directs du sommaire MIA et, si nécessaire, les sous-pages Wikirouge. Les règles de collecte MIA sont bornées (liens du même répertoire pour un index, même radical pour les anciens fichiers, préfaces datées explicitement liées). Elles ne démontrent pas une couverture intégrale. Le moteur ne recherche l'anglais qu'après avoir retrouvé une source française exacte dans les documents examinés ou une variante à revoir. Il privilégie l'URL MIA anglaise explicitement fournie par Wikirouge, et se replie sur les sous-pages anglaises de Wikirouge. Les rangs sont évalués après recherche ; pas de correspondances de chapitres ni d'URL cible présélectionnées. Le pilote conserve le classement lexical et mesure séparément un alignement structurel expérimental lorsqu'un titre de chapitre bilingue permet d'établir une hypothèse unique. Il ne valide ni les limites, ni les droits, ni la fidélité d'une citation.

## Alignement automatique expérimental par titres de sections

Le moteur extrait les titres et libellés des sommaires des pages déjà récupérées. Un petit vocabulaire bilingue de concepts généraux rapproche les titres indépendamment de la citation. Les numéros de chapitre seuls ne suffisent jamais ; les titres génériques, les titres d'œuvres répétés, les ambiguïtés et les longueurs incompatibles sont filtrés. Une hypothèse unique déclenche l'alignement par phrases lorsque la source française est exacte et non ambiguë. Les candidats lexicaux originaux restent inchangés, et les résultats de l'alignement sont mesurés séparément (ancre courte et volume de mots candidats). Ce dictionnaire a été développé en connaissant le corpus pilote : une évaluation sur un corpus distinct demeure indispensable.

## Échantillon découvert automatiquement (hors corpus de développement)

`python inventory.py --replay` interroge des lots bornés de liens externes de Wikidébats vers Marxists.org, puis recense les modèles Citation bruts effectivement liés au site. Le test sélectionne au plus huit œuvres distinctes par un ordre déterministe, en excluant les pages et titres d'œuvres du corpus de développement. La citation est référencée par sa révision, l'indice du modèle et les empreintes du modèle et du texte. Il n'y a plus besoin d'un fragment choisi à la main pour identifier le modèle ; un sélecteur interne est dérivé de la citation seulement pour la recherche approximative. Les réponses attendues ne sont pas fournies au moteur. Les résultats mesurent la couverture de candidats, pas leur exactitude. Les compteurs et empreintes sont publiés, jamais les textes des citations.

## Variantes de noms d'auteurs

Le premier inventaire hors corpus de développement (six œuvres sélectionnées, 24 pages examinées, 40 modèles) a révélé trois conflits de graphie et trois routes bibliographiques non résolues. Le contrôle d'auteur prend en charge une liste explicite et limitée de variantes du nom complet, dont Léon Trotski/Trotsky. Ces deux graphies sont attestées sur la même page de Wikirouge : https://wikirouge.net/texts/fr/Le_d%C3%A9veloppement_du_militarisme_mondial_et_nos_t%C3%A2ches_militaires . Aucune normalisation générale des patronymes, aucune acceptation automatique des citations. Les conflits réels et les ambiguïtés restent bloquants.

## Répertoire bibliographique réutilisable et nouvelle évaluation

Le fichier bibliographic-catalog.json contient des correspondances **recherchées manuellement**, avec URL française exacte, titre, auteur, références bibliographiques, date de recherche et réserves sur les droits. Il ne contient aucun extrait. Trois notices initiales répondent aux blocages du premier inventaire : la lettre à Wendelin Thomas (Wikirouge), la résolution de Stuttgart (édition anglaise MIA de 2021, droits non confirmés) et la traduction de Victor Serge (notice bibliographique seulement, pas de texte accessible vérifié). Une entrée n'est utilisable que si le lien, le titre et l'auteur de la Citation correspondent ; la source française doit ensuite être localisée avant toute recherche anglaise. La notice ne doit jamais lever une ambiguïté ou un conflit d'auteur. Une correspondance retrouvée grâce au répertoire n'est pas une découverte automatique.

`python inventory.py --catalog bibliographic-catalog.json --replay` mesure séparément les parcours automatiques et les parcours assistés sur la même sélection. Le moteur teste aussi l'alignement des documents monoblocs reliés par des métadonnées explicites et concordantes, sans présumer que des chapitres ou des anthologies entières sont parallèles. Tout résultat reste une proposition à vérifier. Lorsque la matrice d'alignement par phrases dépasse le budget, le moteur essaie les paragraphes sans relever la limite. Les documents français déjà examinés sont dédupliqués au sein de chaque recherche ; un lien de citation pointant vers un sommaire continue à déclencher l'examen des pages liées.

Un deuxième test avec `--batches 4 --scan-pages 60 --sample 12 --balance-authors --exclude-file development-inventory.json --max-work-pages 32 --budget 400` examine des lots plus profonds et privilégie une diversité d'auteurs. Les six œuvres du premier inventaire, désormais utilisées pour le développement, sont exclues. Ce nouveau test ne charge pas le répertoire bibliographique et mesure seulement la couverture sur des œuvres non sélectionnées auparavant ; aucune précision éditoriale indépendante n'est encore calculée.

## Correctifs issus du lot élargi

Le premier lot élargi (60 pages, 90 modèles, 8 œuvres) a révélé des sommaires MIA anciens dont le fichier ne s'appelle pas index.htm, notamment Anti-Dühring, La Révolution trahie et le Programme de transition. Le moteur détecte maintenant les libellés explicites de sommaire, examine les liens HTML directs dans le même répertoire même en l'absence de champ MIA URL dans Wikirouge, et déduplique les documents examinés. Une page de texte ordinaire n'est pas assimilée à un sommaire. Le budget du lot élargi passe de 12 à 32 pages par inventaire et de 320 à 400 requêtes globales : il s'agit d'un essai de couverture, pas d'une comparaison à budget égal.

Lorsque la recherche lexicale par paragraphes ne produit aucun candidat, un moteur de secours parcourt des fenêtres de phrases et exploite les ancres bilingues et préfixes de cognats. Aucun texte n'est généré ; les extraits restent candidats et les limites de calcul sont signalées. Les rangs du moteur initial restent inchangés lorsqu'il propose déjà des candidats. La graphie Lénine/Lenin est également traitée explicitement.

## Recherche documentaire classique : comparaison contrôlée

Le répertoire contient également quatre pistes issues de recherches Web sur le lot élargi : Luxemburg (Junius), Trotsky (Bolchevisme ou stalinisme, Programme de transition) et Engels (Anti-Dühring). Une notice peut identifier une page française plus précise que l'URL du sommaire citée ; elle doit être documentée dans les références. Le moteur vérifie le passage français avant de classer l'anglais, et n'effectue un alignement de document complet que si l'occurrence française se trouve sur la page parallèle explicitement désignée. Le parcours automatique est mesuré séparément avant le parcours assisté ; une piste Web n'est pas un résultat indépendant ni une traduction approuvée. Le lot élargi est désormais un corpus de diagnostic, plus un échantillon aveugle.

Le moteur sait aussi rechercher les fragments d'une citation contenant des ellipses explicites, en conservant les positions et la taille des omissions, puis exploiter plusieurs groupes de quatre mots uniques si le sélecteur initial a changé. Les budgets et ambiguïtés sont signalés ; jamais de promotion de ces variantes en citation contiguë exacte. Les rapports contiennent uniquement les empreintes, métadonnées, coordonnées et compteurs, sans texte de citation. Les comparaisons sont reproductibles hors ligne.

## Avant intégration au dépôt principal

Confirmer les résultats sur les sources réelles, élargir le corpus indépendant et les cas négatifs, évaluer les droits et les limites des citations, exploiter les métadonnées des modèles Citation bruts, mesurer le coût réel. Ne pas relâcher les critères pour obtenir une CI verte. L'intégration au workflow de traduction fera l'objet d'une décision séparée.

## Local Wikirouge snapshot (experimental)

The dated archive in `data/wikirouge/` is the original metadata snapshot, not a full-text corpus. The manifest binds the compressed archive, the SQLite member and its schema/counts. The loader extracts only that member, verifies both SHA-256 digests, and opens SQLite read-only. It never executes bundled Python code.

```sh
python auto_retrieve.py --local-index data/wikirouge/manifest.json --replay
python benchmark_index.py --sample 50
```

The legacy CLI remains unchanged unless the index option is used. The indexed route requires a unique native pair, compatible authors on both sides and no conflicting known writing year or URL/title evidence. One-way links are labelled as such. Ambiguous mappings are retained in diagnostics and fall back to the legacy navigation. A subpage can inherit a parent's *work* route but never proves chapter identity. Mirror URLs and bibliographic fields are retained verbatim; only the existing MIA/Wikirouge allowlist is used for fetching. Neither a work candidate nor an aligned text window constitutes a verified translation, edition, translator, boundaries or reuse rights.

The paired benchmark selects pinned real citations with distinct quotation fingerprints and prioritized work and author diversity, prioritizes independent cases over known development examples, and admits only French Citation templates whose own lien parameter contains a canonicalizable marxists.org URL. It permits multiple distinct quotations from the same work, reporting development overlap if needed. It never fills a shortfall with non-MIA cases. Both arms share the selected input revisions, deterministic retrieval and per-case bounds but use separate initially empty caches; arm order alternates. Discovery cost is reported separately. Results are written after each pair; the run fails if fewer cases than requested were available. The benchmark reports work/source/target/alignment candidates, network requests, decoded response-body bytes (not wire bandwidth), runtime, errors, and deduplicated candidate words for review (not LLM tokens). Exact-quote verification, edition/translator and rights require independent documentary review, so unmeasured verified counts and false positives are null. No model calls, wiki writes, publication or production integration occur.


## MIA-only refinement (development experiment)

The second paired run uses the exact frozen 50-citation cohort in GitHub Actions run 45713244614, verified by report SHA-256. Both arms use the same verified Wikirouge snapshot, empty separate caches, and the same enlarged per-case/page bounds. The refined arm uses researched document leads, cached direct parallel-document alignments (including a strictly checked unique source variant/omission envelope), and a first-pass review packet prioritizing one aligned or lexical candidate. It never approves exact boundaries or removes fallback candidates. No generated translation or model call is used.

Five new catalog routes were identified by inspecting the development cohort. The Plekhanov 1904 Geneva lectures must not be conflated with his distinct 1897 text under the same title. Modern copyright notices on the Rakovsky and Harman targets are recorded, not treated as permission to reuse. Bibliographic and passage candidates remain unverified.

```sh
python auto_retrieve.py --local-index data/wikirouge/manifest.json --catalog bibliographic-catalog.json --refined
python benchmark_refine.py --baseline-report paired-index.json
```

The refinement comparison is a development recheck, not an independent holdout. The baseline artifact can expire on GitHub; keep a local copy of the original paired-index.json or use the preserved copy from the refinement artifact. The first-pass metric measures English words offered initially, not correctness, total review effort, or AI quota; all deferred candidates remain accessible. Separate exact-quotation adjudication and a fresh holdout are still necessary.

### Long French passages and explicit omissions (experimental refinement)

The refined path may retry long quotations up to 600 normalized words using banded prefix edit distance, or explicit omission fragments using ordered same-document spans. Edge ellipses and approximate fragments remain review-only evidence. Each retained fragment keeps actual source coordinates. No implicit gap, cross-document join, or fabricated English text is allowed. The older comparison arm retains the original search limits.

The catalog can record explicit title aliases on the same exact source URL. Refined catalog routes restricted to a particular source document require that the French quotation is actually located there before retrieving the target. This guards the new 1859 Marx preface mapping against a misleading whole-book citation link. The 1881 Engels Trades Unions route covers both articles on the English archive page; verify the French passage and edition before reuse.

### Fresh MIA quotation holdout

```sh
python benchmark_refine.py --baseline-report paired-index.json --fresh-holdout --out reports-live/holdout-index.json
```

This excludes the exact quotation and template fingerprints of all 50 frozen development cases, selects at least 50 new MIA-linked quotation records without using retrieval outcomes, and prioritizes previously unseen pages, work labels and exact source URLs. It reports results separately for unseen and familiar-document groups. Bibliographic aliases and researched catalogue URLs are part of the known set. It does not claim semantic deduplication of paraphrased quotations, a fully work-disjoint sample when insufficient records exist, or verified translation accuracy. It fails rather than silently lowering the requested sample size or adding non-MIA records.

### Independent evidence audit

```sh
python evidence_audit.py --report refined-index.json --sha256 <pinned-report-sha256>
```

The evidence auditor uses a new empty HTTP cache. It reopens the actual pinned French Citation template, checks its fingerprints and MIA URLs, rereads every distinct retrieved source document once, and reconstructs each French and English candidate from recorded character or block coordinates. Document hash, revision and extracted-text hash are checked separately. An identical excerpt in changed HTML is distinguished from strict document-and-span verification. No candidate text is printed or stored in the public audit artifact. Labelled bibliographic fields and page-level license signals are collected once per document. A license signal is not an authorization to reuse a translation; bilingual equivalence, precise English quotation boundaries and license compatibility remain separate unreviewed decisions. Audit completeness means the checks were performed, not that every check passed.


### Targeted chapter quality

The first 100 MIA cases exposed a failure mode: lexical scoring may prioritize a different chapter of the correct work. The optional `--chapter-catalog chapter-catalog.json` in refined mode uses independently checked bilingual tables of contents and a unique exact French source occurrence (same cited leaf or reviewed TOC and confirmed author) to restrict retrieval to the known chapter. It does not silently substitute a different chapter when the mapped chapter is unavailable. It never labels a structural match an exact translation.

`chapter_quality.py` independently rereads the two pinned reports from run 45715249856 and measures this targeted correction only on cases eligible for the reviewed mappings. This is not a new independent whole-corpus benchmark. It separately revalidates the first explicit bilingual review recorded in `reviewed-quotations.json` against the pinned French input and exact source/target document and span hashes. The review is a documentary judgment, not an automated inference; the original MIA English edition metadata is retained, but rights remain unapproved. No full quotations are stored in the review registry.
