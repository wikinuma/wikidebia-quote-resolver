# Campagne autonome de préparation anticipée

Sur le serveur (Python 3.10+ sous Linux), dans un clone de la branche
expérimentale, installer les dépendances et choisir un répertoire privé,
persistant, hors du dépôt :

    python -m pip install -r requirements-navigation.txt
    STATE="$HOME/.local/share/wikidebia-quote-resolver"
    python prepare_citations.py --state "$STATE" inventory
    python prepare_citations.py --state "$STATE" search --limit 0 --budget 5000
    python prepare_citations.py --state "$STATE" status

La commande search est relançable : elle ne reprend que les occurrences en
attente, avec un budget de requêtes global et un budget par occurrence.
Le cache HTTP, les correspondances bibliographiques et les alignements sont
partagés. Aucun filtrage par fréquence ni longueur lors de la sélection.
Les limites internes du moteur restent explicites. Les erreurs ne sont
pas interprétées comme une absence de traduction.

    python prepare_citations.py --state "$STATE" search --retry-documentary --limit 0
    python prepare_citations.py --state "$STATE" search --reconsider --limit 0 --max-pages 128

Les deux commandes ciblent exclusivement les échecs documentaires et les
preuves insuffisantes, respectivement. Elles sont des tentatives explicites,
pas une boucle de réessai sans fin. Les résultats des tentatives restent
dans la base.

Pour la revue linguistique groupée (hors Codex) :

    python prepare_citations.py --state "$STATE" review
    python prepare_citations.py --state "$STATE" apply /chemin/reponse.json

Transmettre seulement le fichier review-*.json indiqué par la commande,
en vérifiant les droits des passages avant transfert. Le manifeste complet
reste dans SQLite, scellé par empreinte. Les paquets sont regroupés par
ouvrage et chapitre ; chaque occurrence garde sa propre question, même
en cas de doublon exact entre pages. Les passages candidats communs sont
affichés une fois. Le retour ne contient que les identifiants, décisions et
bornes. Une relance de review restitue le même paquet ouvert ; apply
vérifie à nouveau les documents sur les sources publiques. Les paquets sont
bornés et automatiquement divisés lorsque nécessaire. Les abstentions
restent des preuves insuffisantes, pas des échecs définitifs.

Après une recherche documentaire effectivement terminée et un jugement
explicite de la portée examinée :

    python prepare_citations.py --state "$STATE" close-negative ID --reason "Portée effectivement examinée et motif"

Cette clôture est une décision opérationnelle, pas une preuve d'inexistence
de toute traduction. Elle n'est jamais inférée d'un simple score ou d'un
budget épuisé.

La base persistante est works.sqlite3 dans STATE. Pour importer les décisions
historiques, réutiliser historical_citations.py avec les artefacts d'origine
et --db "$STATE/works.sqlite3" (voir HISTORICAL-CITATIONS.md), avant search.
Le nouveau passage est reconstitué uniquement si le reçu existant correspond
encore au document anglais relu. Cette preuve n'autorise pas sa publication.
La commande inventory refuse un rafraîchissement tant qu'un paquet est
ouvert ; l'inventaire incomplet ne remplace pas l'ancien. Les anciennes
occurrences et décisions restent dans la base, tandis que status ne compte
que l'inventaire courant. duplicate-anomalies.json signale les citations
textuellement identiques sur des pages différentes, sans exclure d'occurrence.

Les états reviewed_receipt_rights_unreviewed, review_pending, awaiting_review,
retry_documentary, insufficient_evidence et closed_without_result ne sont
pas interchangeables. Ni le registre, ni les paquets ne constituent une
validation des droits ou une intégration au kit. Aucune écriture sur les wikis,
aucun modèle hébergé, aucun secret requis. Ne pas publier les fichiers privés
de STATE, y compris le cache HTTP, la base et les paquets.
