#!/usr/bin/env bash
# kill-dockers.sh — tear down every Docker resource created by SessionBridge.
#
# It targets any docker-compose stack launched from THIS repo directory,
# regardless of project name (the default `sessionbridge` plus any `sb2`, `sb3`,
# … test stacks), by matching the compose `working_dir` label.
#
#   ./kill-dockers.sh                # remove containers + app networks
#   ./kill-dockers.sh --volumes      # also remove named volumes (deletes stored PDFs)
#   ./kill-dockers.sh --images       # also remove the built images
#   ./kill-dockers.sh --all          # containers + networks + volumes + images
#   ./kill-dockers.sh --dry-run      # show what would be removed, change nothing
set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
WD="com.docker.compose.project.working_dir=$REPO_DIR"

RM_VOLUMES=0 RM_IMAGES=0 DRY=0
for a in "$@"; do
  case "$a" in
    --volumes)  RM_VOLUMES=1 ;;
    --images)   RM_IMAGES=1 ;;
    --all)      RM_VOLUMES=1; RM_IMAGES=1 ;;
    --dry-run)  DRY=1 ;;
    -h|--help)  sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (try --help)"; exit 2 ;;
  esac
done

# Wrap docker calls in a timeout: some daemons (notably the snap package under
# AppArmor) HANG on stop/rm instead of returning an error. timeout turns that
# into a fast, recoverable failure so the script never freezes.
DOCKER_TIMEOUT="${DOCKER_TIMEOUT:-15}"
d() { timeout "$DOCKER_TIMEOUT" docker "$@"; }
run() { if [ "$DRY" = 1 ]; then echo "  would: docker $*"; else d "$@" >/dev/null 2>&1; fi; }
blocked=0

command -v docker >/dev/null 2>&1 || { echo "docker not found"; exit 1; }
hdr=""; [ "$DRY" = 1 ] && hdr=" [dry-run]"
echo "SessionBridge cleanup  (repo: $REPO_DIR)$hdr"

# --- containers -----------------------------------------------------------
cids=$(docker ps -aq --filter "label=$WD")
projects=$(docker ps -a --filter "label=$WD" \
             --format '{{.Label "com.docker.compose.project"}}' | sort -u)
# include the default project name even if it has no containers right now
projects=$(printf '%s\n%s\n' "$projects" "$(basename "$REPO_DIR")" | sort -u | sed '/^$/d')

if [ -n "$cids" ]; then
  echo "Containers:"
  docker ps -a --filter "label=$WD" --format '  - {{.Names}} ({{.Status}})'
  for c in $cids; do
    if [ "$DRY" = 1 ]; then echo "  would: docker rm -f $c"; continue; fi
    d rm -f "$c" >/dev/null 2>&1 && continue
    # snap-docker may refuse or HANG on stop; try kill, then a plain rm.
    d kill "$c" >/dev/null 2>&1
    d rm "$c"   >/dev/null 2>&1 || { echo "  ! could not remove $c"; blocked=1; }
  done
else
  echo "No containers found for this repo."
fi

# --- networks -------------------------------------------------------------
echo "Networks:"
found_net=0
for p in $projects; do
  for n in $(d network ls -q --filter "label=com.docker.compose.project=$p"); do
    found_net=1
    name=$(d network inspect "$n" --format '{{.Name}}' 2>/dev/null)
    echo "  - $name"; run network rm "$n"
  done
done
[ "$found_net" = 0 ] && echo "  (none)"

# --- volumes --------------------------------------------------------------
if [ "$RM_VOLUMES" = 1 ]; then
  echo "Volumes:"
  found_vol=0
  for p in $projects; do
    for v in $(d volume ls -q --filter "label=com.docker.compose.project=$p"); do
      found_vol=1; echo "  - $v"; run volume rm "$v"
    done
  done
  [ "$found_vol" = 0 ] && echo "  (none)"
else
  echo "Volumes: kept (use --volumes to remove)"
fi

# --- images (named <project>-<service>) -----------------------------------
if [ "$RM_IMAGES" = 1 ]; then
  echo "Images:"
  found_img=0
  for p in $projects; do
    for id in $(d images "${p}-*" -q | sort -u); do
      found_img=1
      tag=$(d image inspect "$id" --format '{{join .RepoTags ","}}' 2>/dev/null)
      echo "  - ${tag:-$id}"; run rmi -f "$id"
    done
  done
  [ "$found_img" = 0 ] && echo "  (none)"
else
  echo "Images: kept (use --images to remove)"
fi

if [ "$blocked" = 1 ]; then
  echo
  echo "Some containers could not be removed — the Docker daemon refused or timed"
  echo "out on stop/rm. This happens with the snap-packaged daemon under AppArmor."
  echo "Fix the daemon, then re-run this script:"
  echo "    sudo snap restart docker     # snap install"
  echo "    sudo systemctl restart docker # apt/deb install"
  exit 1
fi
if [ "$DRY" = 1 ]; then echo "Done. (dry-run — nothing changed)"; else echo "Done."; fi
