# Wikirouge — export bibliographique public v2

Aucun compte ni accès SSH Wikirouge nécessaire. L'exécution combine les propriétés Semantic MediaWiki (ask), les identifiants des révisions, les interlangues natives et les liens externes (extlinks). Elle ne demande ni wikicode, ni contenu des révisions, ni texte intégral. Le miroir marxists.info est conservé séparément de marxists.org. Tous les liens externes MIA sont des **candidats bibliographiques** ; plusieurs liens ne sont jamais réduits arbitrairement à une source.

Sur Dialectica, dans le répertoire wikirouge-export-cible déjà installé :

    .venv/bin/python -m unittest -v test_export_semantique_v2.py
    .venv/bin/python export_semantique_v2.py --probe

Le sondage doit trouver cinq URL connues, les auteurs et dates, et les deux correspondances bilingues connues. Les publications non indexées ne bloquent plus le sondage. Après accord du responsable du site, ne lancer qu'un export à la fois :

    nohup .venv/bin/python -u export_semantique_v2.py --full > export-v2.log 2>&1 &

    tail -f export-v2.log

Ne pas utiliser --login. Le délai minimal est de deux secondes, avec 25 pages par lot, sans parallélisme. Le nouveau dossier export-semantique-v2 est indépendant des anciens checkpoints. Relancer la même commande en cas d'interruption. Un verrou empêche deux processus de modifier le même checkpoint. Le budget par lancement est de 6000 requêtes ; les lots enregistrés ne sont pas refaits. En cas d'erreur SMW sur un gros lot, reprendre avec --batch 10 dans le même dossier.

Transmettre seulement export-semantique-v2/wikirouge-semantic-metadata.json.gz, ou le rapport de sondage si celui-ci échoue. Le champ export_complete_for_declared_scope concerne la couverture des catégories recensées, non la présence de toutes les propriétés ou l'existence d'une traduction vérifiée. Le rapport distingue les liens MIA indexés et externes, les ambiguïtés et les interlangues sans cible dans l'inventaire. Les détails de publication ne sont disponibles que s'ils sont indexés. Aucune publication ou modification wiki.
