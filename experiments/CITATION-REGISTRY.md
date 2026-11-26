# Registre local des citations préparées (expérimental)

Le registre est réservé aux modèles Citation dont le **paramètre littéral
`|lien=`** désigne Marxists.org. Il ne rétablit pas l'ancien raccourci fondé
sur les étiquettes de benchmark. Les 38 décisions historiques restent des
données d'évaluation jusqu'à vérification et revue explicites; aucune
importation automatique.

`CitationRegistry(library.db)` utilise la base SQLite privée de WorkLibrary.
Les clés de contenu distinguent citation, wikitexte et métadonnées complètes.
Une modification du reste de la page, un déplacement ou un changement d'ordre
des paramètres ne rend pas caduque une citation inchangée. Une modification du
texte, de l'attribution, de l'édition, du lien littéral ou d'un autre paramètre
invalide prudemment la réutilisation. La référence brute (révision, index,
empreinte de modèle) est contrôlée à chaque accès.

Le flux de preuve est explicite : `prepare_review_source` identifie le modèle
et le candidat anglais; le réviseur fournit les deux identifiants, une empreinte
de paquet, sa provenance, la décision et les bornes relatives. `record`
vérifie le passage exact dans le document fourni et extrait les empreintes
des fragments. Aucun texte de citation n'est écrit dans la table. Les
décisions incompatibles concernant une même source empêchent toute sélection
automatique; une même décision est idempotente. `reconstruct` ne restitue les
fragments qu'en présence du document anglais exact et de la source intacte.
Les résultats négatifs, partiels et variantes ne constituent pas des preuves
positives.

**Limites** : le registre n'est pas encore branché au moteur de recherche ni
au kit. L'appelant doit fournir un document réellement relu, une révision FR
authentifiée et un jugement éditorial indépendant; ces fonctions vérifient les
octets et les bornes, pas la fraîcheur de la lecture, l'identité du réviseur,
l'équivalence linguistique ou les droits. La provenance n'est pas une
signature cryptographique. Aucun résultat n'est prêt à publier; droits et
revue éditoriale restent requis. Le stockage d'échecs datés, le transport
privé des paquets et l'intégration normative sont des étapes ultérieures.

Une nouvelle preuve exacte du même passage d'une révision Wikirouge positive
(même URL, révision, coordonnées et empreintes des fragments) ne crée pas un
conflit du seul fait d'un changement de HTML. La reconstruction exige toujours
un reçu qui correspond aux octets et aux blocs exacts du document fourni.
Sans ce reçu, elle échoue même si une décision historique est connue. Les
changements de révision, de passage ou d'identité d'un document non versionné
restent des conflits à revoir. Cette distinction ne constitue pas une validation
de fraîcheur ou une autorisation de réutilisation.
