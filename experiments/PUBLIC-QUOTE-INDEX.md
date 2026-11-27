# Index des citations publiées (diagnostic global)

Un contrôle de doublons doit aussi comparer les citations provenant d'autres
débats et celles des pages anglaises réutilisées. public_quote_index.py
réemploie la pagination et les lectures groupées du recensement des domaines
de liens, pour Modèle:Citation (FR) et Template:Quote (EN), redirections de
modèles comprises. Toutes les occurrences littérales dans l'espace principal
des pages énumérées sont examinées, sans filtre de fournisseur. Les pages
avec Argument sont distinguées des autres; aucun autre site n'est interrogé.
Aucun texte de citation ni cache HTTP n'est conservé dans l'index.

    python public_quote_index.py --language fr --out "$PRIVATE/fr-index.json"
    python public_quote_index.py --language en --out "$PRIVATE/en-index.json"

Le SHA-256 canonique du fichier (objet JSON sans saut de ligne final) figure
dans le résumé. Les fichiers sont privés: ne jamais les committer. En CI
seuls les compteurs agrégés sont publiés. Chaque page a un identifiant
MediaWiki, un titre et une révision; chaque occurrence a un rang, une
empreinte du texte littéral normalisé (NFC, espaces) et une empreinte des
mots (casse/ponctuation et ellipses explicites). Les modèles/formatages wiki
ne sont pas développés; l'égalité de texte rendu malgré un wikicode différent
n'est pas garantie. La seconde empreinte fournit un signal, non une preuve
d'identité éditoriale. Les fragments discontinus ne sont pas réunis
artificiellement. Les citations hors modèle, transclusions indirectes et
paraphrases ne constituent pas un périmètre exhaustif.

## Actualisation économique

    python public_quote_index.py --language fr --previous "$PRIVATE/fr-index.json" \
      --previous-sha256 "$OLD_SHA" --out "$PRIVATE/fr-index-next.json"

L'index antérieur doit être compatible et vérifié. Même un index incomplet
peut servir à reprendre les seules pages individuellement valides : le
recensement des pages est refait, leurs révisions courantes sont demandées
par lots et toute page absente ou contenant une occurrence invalide est relue.
Une page valide inchangée (même ID, titre et révision, mêmes alias) n'est pas
téléchargée ni reparsée. Les nouvelles, renommées ou modifiées sont lues;
celles qui ne figurent plus dans le recensement sont retirées.
Un échec de lecture ne réutilise jamais une entrée ancienne. Les erreurs
d'énumération, paramètres manquants/ambigus et limites de requêtes rendent
l'index incomplet. Aucun instantané non atomique ne garantit l'état courant
du wiki à une date ultérieure. Ne pas convertir l'absence d'un doublon
dans un index ancien ou incomplet en attestation de publication.

La comparaison globale est distincte du registre des correspondances
préparées et du contrôle lexical inter-lots: elle ne transporte pas de
traduction, n'accorde aucun droit et ne modifie aucune page. Le code ne
recense pas les débats d'appartenance: une page distincte suffit à signaler
un doublon, y compris entre débats; un argument partagé est une seule page.

Le bilan distingue désormais la lecture intégrale des pages énumérées de
l'indexabilité des occurrences littérales. Les paramètres absents, répétés
ou vides sont comptés séparément, sans sortir leur texte. Même si toutes
les pages ont été lues, une seule occurrence invalide maintient le statut
incomplete et le code de sortie non nul. Un diagnostic partiel ne devient
pas un certificat de complétude.
