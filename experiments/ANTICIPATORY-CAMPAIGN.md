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


En cas de document modifié entre préparation et import, apply refuse
le paquet. Pour sortir de cet état sans supprimer l'historique :

    python prepare_citations.py --state "$STATE" cancel-review --reason "Preuves devenues obsolètes"
    python prepare_citations.py --state "$STATE" search --retry-documentary --limit 0

Une citation inchangée sur la même page conserve les tentatives antérieures
même si l'identifiant de révision ou l'index du modèle change (appariement
unique par page ID et identité de contenu, à algorithme inchangé).
Aucun transfert entre pages. Les reçus positifs sont relus avant réutilisation.

    python prepare_citations.py --state "$STATE" cases --filter insufficient_evidence
    python prepare_citations.py --state "$STATE" search --recheck-reviewed --limit 0

La première commande liste les identifiants et diagnostics sans texte de
citation ; la seconde réévalue explicitement les reçus déjà retrouvés.


## Revue des passages élargis

Le moteur de recherche n'est pas relancé : la préparation relit le document
anglais exact, vérifie l'empreinte du passage initial, puis ajoute du contexte
des deux côtés selon la longueur de la citation française, dans le même
document. Les fenêtres se chevauchant sur le même document exact sont
mutualisées si leur union respecte la limite. Les coordonnées seed_ranges
rappellent les ancres d'origine, sans limiter la sélection à ces ancres.
Les offsets du retour restent relatifs au passage partagé complet. Les
bornes de sécurité, l'abstention et la revalidation avant import restent
inchangées. Les anciennes preuves non reproductibles ne sont pas élargies.

Pour reprendre les abstentions des anciens paquets sans nouvelle recherche :

    python prepare_citations.py --state "$STATE" requeue-context
    python prepare_citations.py --state "$STATE" review

Les reçus acceptés, les échecs documentaires et les recherches sans candidat
ne sont pas modifiés par requeue-context. La commande est idempotente ; les
abstentions des nouveaux paquets ne sont pas automatiquement remises en file.
Les compteurs context_stats mesurent le volume de caractères des fenêtres
avant/après mutualisation, pas les tokens IA ni la qualité linguistique.


## Revue par vagues, sans transferts individuels

    python prepare_citations.py --state "$STATE" review-many --packets 8

La commande prépare jusqu'à huit paquets, avec un seul auditeur frais,
et crée une archive ZIP privée review-wave-*.zip. La transmettre à ChatGPT
classique seulement si les droits de partage des extraits le permettent.
Le fichier index.json donne l'ordre des paquets et leurs empreintes ;
texts.json mutualise les fenêtres qui se chevauchent sur le même document
exact, y compris entre paquets. Les fichiers packets/<empreinte>.json
contiennent des références vers ces fenêtres. Pour reconstituer le paquet
autonome numéro 1 sans extraction :

    python review_bundle.py /chemin/review-wave.zip --packet 1

Chaque sélection conserve les offsets Unicode relatifs au passage original
reconstitué, et non à la fenêtre commune. L'empreinte du paquet local ne
change pas. Les textes ne sont pas inclus dans les réponses.

Importer un tableau JSON de réponses ordinaires (une par paquet complet),
éventuellement directement depuis le terminal :

    python prepare_citations.py --state "$STATE" apply-many - <<'EOF'
    [ ... réponses JSON complètes ... ]
    EOF

Une réponse invalide n'empêche pas l'import des autres paquets valides.
La commande affiche le résultat par empreinte et retourne un code non nul
si un paquet a été rejeté. Les réponses identiques déjà importées sont
idempotentes. Il n'est pas nécessaire de répondre à toute la vague d'un
coup. review-many réexporte d'abord les paquets encore ouverts ; elle ne
crée pas de nouveaux paquets tant qu'une vague précédente reste ouverte.
Le mode review/apply individuel reste utilisable. Pour annuler un paquet
périmé quand plusieurs sont ouverts, préciser son empreinte :

    python prepare_citations.py --state "$STATE" cancel-review --packet-sha EMPREINTE --reason "Preuves obsolètes"

Les lectures et le partage des fenêtres sont mutualisés dans la vague,
pas dans la totalité du corpus. Les économies en caractères de l'archive
ne sont pas une mesure du coût IA. Les paquets restent bornés pour ne pas
confondre la capacité à transférer un fichier avec la capacité à vérifier
correctement tous ses passages.
