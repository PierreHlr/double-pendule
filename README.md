# Double pendule interactif

Application Python permettant de simuler et comparer un ou plusieurs doubles pendules plans. On choisit les angles de départ avant de lancer la simulation, puis deux cartes de chaleur aident à explorer le plan des angles initiaux : l'une mesure à quel point deux pendules presque identiques s'éloignent, l'autre repère les couples d'angles qui donnent un mouvement presque périodique.

![Deux doubles pendules partis à 0,5° d'écart, dont les trajectoires se séparent](docs/simulation.jpg)

## Installation

Il faut Python 3.11 ou plus récent, avec `tkinter` (inclus dans l'installateur Windows de [python.org](https://www.python.org/downloads/)). L'application a été développée et testée sous Windows 11 avec Python 3.14.

Depuis le dossier du projet, créez un environnement virtuel puis installez les dépendances :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Lancer l'application

```powershell
.\.venv\Scripts\python.exe .\main.py
```

Sous macOS ou Linux, remplacez `.\.venv\Scripts\python.exe` par `.venv/bin/python`.

L'interface n'utilise que la bibliothèque standard de Python (`tkinter`). Les cartes de chaleur demandent en plus NumPy ([`requirements.txt`](requirements.txt)) ; sans lui, l'application fonctionne mais les deux vues de cartes affichent un message d'installation.

## Déroulement

1. **Réglage.** L'application s'ouvre à t = 0, voyant « RÉGLAGE » allumé. On choisit θ₁ et θ₂ de chaque pendule :
   - en faisant glisser une masse (pas de 0,5°, 0,1° avec Maj) ;
   - avec les flèches du clavier : ← → pour θ₁, ↑ ↓ pour θ₂ (1°, 0,1° avec Maj, 10° avec Ctrl) ;
   - en cliquant sur une valeur du panneau pour la saisir (Entrée valide, Échap annule, Tab passe au champ suivant), ou avec la molette au-dessus de la valeur ;
   - en cliquant sur une carte de chaleur, ou sur un des mouvements presque périodiques proposés.

   Les modifications s'appliquent au pendule sélectionné (clic sur sa carte, ou Tab). Les angles de départ sont dessinés sur la scène.
2. **Lancement.** `Espace` démarre la simulation depuis les angles choisis (un double-clic sur une carte de chaleur fait de même).
3. **Retour au réglage.** `R` revient à t = 0 en conservant les angles choisis. En réglage, `R` rétablit les angles de `simulation_config.py`.

Seuls les angles se changent dans l'interface : longueurs, masses, gravité, amortissement et couleurs restent dans [`simulation_config.py`](simulation_config.py).

## Vues

Les onglets en haut de la scène, ou les touches `1`, `2` et `3` (ou `&`, `é`, `"` sur un clavier AZERTY, ainsi que le pavé numérique), changent de vue.

- **Pendules** : rapporteur gradué centré sur le pivot (0° correspond à la verticale descendante), traînées qui s'estompent avec le temps, masses lumineuses.
- **Divergence** : pour chaque couple (θ₁, θ₂) de départ, l'exposant de Lyapunov λ mesure la vitesse à laquelle deux pendules presque identiques s'écartent : leur écart croît comme e^{λt}. Un pendule jumeau part à 10⁻⁸ rad près, et leur écart est remis à sa taille initiale toutes les 0,1 s (méthode de Benettin). Sombre : mouvement régulier ; clair : chaotique.
- **Périodicité** : pour chaque couple de départ, plus petit écart entre l'état initial et un état ultérieur (angles modulo 360° et vitesses), rapporté à la distance entre l'état initial et l'équilibre. Un mouvement parfaitement périodique repasse exactement par son départ. Clair : presque périodique. Les minima les plus marqués sont affinés par recherche locale, puis proposés dans une liste avec leur période.

<p>
  <img src="docs/carte-divergence.jpg" alt="Carte de divergence : îlot régulier sombre au centre, zone chaotique claire autour" width="49%">
  <img src="docs/carte-periodicite.jpg" alt="Carte de périodicité : courbes claires des modes propres et liste des mouvements presque périodiques" width="49%">
</p>

Survoler une carte affiche la valeur de la cellule ; un clic applique le couple d'angles au pendule sélectionné. Chaque carte utilise les longueurs et masses du pendule sélectionné.

Les cartes sont calculées en quelques secondes sur plusieurs processus, avec NumPy, puis gardées dans le dossier `.cache/` : les ouvertures suivantes sont immédiates. La grille, la durée simulée et le pas de calcul se règlent dans `SETTINGS`. Le système étant symétrique par réflexion gauche-droite, seule la moitié de la grille est calculée.

## Interface

- **En-tête** : titre, voyant réglage/lecture/pause et chronomètre de la simulation. Une notification brève y confirme certaines actions.
- **Panneau de configuration** : une carte par double pendule, avec les couleurs de ses deux masses, ses angles initiaux et ses longueurs. Les cartes passent en format compact lorsqu'il y a beaucoup de pendules.
- **Barre des touches** : rappel des raccourcis du mode en cours ; les bascules actives (traînées, plein écran) sont colorées.

L'affichage reste net sur les écrans haute résolution et la barre de titre adopte le thème sombre sous Windows 11. Tk ne lissant pas les formes du canvas, les éléments ronds (masses, coins arrondis, voyants, repères, icône) sont calculés pixel par pixel et encodés en PNG par [`canvas_graphics.py`](canvas_graphics.py). Les couleurs et le formatage des nombres sont regroupés dans [`theme.py`](theme.py).

## Configuration dans le code

La constante `PENDULUMS` contient tous les doubles pendules à afficher. Chaque entrée définit les masses, longueurs, angles et vitesses initiales, gravité, amortissement et couleurs. Les angles indiqués sont ceux proposés au démarrage. Ajoutez ou dupliquez une entrée pour superposer plusieurs simulations.

`SETTINGS` contrôle le pas de calcul, la vitesse de lecture, la longueur des traînées et les cartes (`map_resolution`, `map_duration`, `map_time_step`).

## Commandes

En réglage :

- `Espace` : lancer la simulation ;
- `← →` / `↑ ↓` : régler θ₁ / θ₂ du pendule sélectionné ;
- `Tab` / `Maj+Tab` : pendule suivant / précédent ;
- `R` : rétablir les angles de `simulation_config.py`.

Pendant la simulation :

- `Espace` : pause ou lecture ;
- `R` : revenir au réglage des angles ;
- `T` : afficher ou masquer les traînées.

À tout moment :

- `1`, `2`, `3` : vue des pendules, de divergence, de périodicité ;
- `H` : afficher ou masquer la barre des touches ;
- `F` : activer ou quitter le plein écran ;
- `Échap` : quitter l'application.

## Modèle physique

Les angles sont mesurés depuis la verticale descendante. Le calcul numérique utilise un intégrateur de Runge-Kutta d'ordre 4.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```
