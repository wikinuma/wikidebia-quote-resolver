# Transfert privé du registre préparé (v1)

Le registre des décisions linguistiques existe déjà dans la table
prepared_citations_v1 de works.sqlite3. La commande registry_transfer.py
transporte uniquement ces reçus; elle ne crée ni nouvelle base de citations ni
nouveau moteur, et ne copie pas les caches, fichiers de revue, pages wiki ou
textes des citations. Les identités du texte source, des métadonnées, de
l'occurrence historique, du document cible et des fragments sont conservées.
Les droits restent non examinés et la publication automatique interdite.

Depuis l'environnement de campagne:

    python registry_transfer.py --state "$CAMPAIGN_STATE" export --out "$PRIVATE/receipts.json"

Le résumé fournit l'empreinte canonique SHA-256 du bundle (même sérialisation
que les reçus existants, sans le saut de ligne final). Transférer le fichier
privé et communiquer l'empreinte par un canal de confiance indépendant.
Ne jamais le committer, le publier comme artefact CI ou l'envoyer à un agent
si un simple résumé suffit. Les URL, identifiants de révision et le libellé
du réviseur restent des métadonnées privées. Utiliser un libellé de réviseur
sans secret ni information personnelle inutile.

Depuis l'environnement de traduction:

    python registry_transfer.py --state "$CODEX_STATE" import --bundle "$PRIVATE/receipts.json" --sha256 "$SHA"
    python translation_handoff.py --state "$CODEX_STATE" prepare --request "$REQUEST" --lookup-only

L'import est transactionnel et idempotent. Les versions inconnues, champs
supplémentaires, preuves incohérentes et empreintes invalides sont refusés.
Les reçus contradictoires restent présents: la résolution existante s'abstient
plutôt que de choisir une édition arbitraire. Aucune ancienne preuve n'est
considérée fraîche du seul fait du transfert. La relecture du verrou source et
du document cible demeure obligatoire; une cible modifiée ne fournit pas de
texte ancien. Un SHA-256 n'authentifie pas un auteur: n'importer que les
reçus de son propre environnement de préparation de confiance.

## Export incrémental et sélection

Dans l'environnement destinataire:

    python registry_transfer.py --state "$CODEX_STATE" inventory --out "$PRIVATE/known.json"

Puis, dans l'environnement source, exporter uniquement ce que le destinataire
n'a pas annoncé posséder:

    python registry_transfer.py --state "$CAMPAIGN_STATE" export --known-inventory "$PRIVATE/known.json" --out "$PRIVATE/delta.json"

L'option répétable --source-key restreint l'export aux identités stables
retournées par l'inventaire MIA ou le handoff. Elle est compatible avec
--known-inventory. Une sélection vide peut être passée à l'API Python;
le CLI sans option de sélection exporte toutes les correspondances.
Ne pas déduire d'une sélection vide qu'aucune traduction n'existe. Il n'y a
pas de synchronisation des suppressions: la v1 transporte des reçus
append-only; les conflits nécessitent une décision explicite ultérieure.

Le format JSON canonique compact est volontairement simple, borné à 50 000
reçus et 64 Mio par fichier, sans ZIP ni SQLite à importer. Le coût réel
dépend du nombre de reçus, non de la taille des œuvres; aucune économie de
tokens IA n'est déduite de cette seule taille. L'intégration automatique de
la sélection depuis le kit et la politique juridique restent à réaliser.
