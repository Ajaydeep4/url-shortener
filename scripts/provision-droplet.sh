#!/usr/bin/env bash
# Provision one environment droplet on DigitalOcean, ready to receive deploys
# from the GitHub Actions "Deploy" workflow.
#
#   ./scripts/provision-droplet.sh <env> [region] [size]
#
#   env     dev | stage | prod
#   region  default: blr1 (Bangalore)
#   size    default: s-1vcpu-1gb for dev, s-1vcpu-2gb for stage,
#           s-2vcpu-4gb for prod
#
# Prerequisites (one-time):
#   - doctl auth init                       (DigitalOcean API token)
#   - an SSH key uploaded to DO:  doctl compute ssh-key list
#     (set SSH_KEY_NAME below or export SSH_KEY_NAME)
#   - export GHCR_USER / GHCR_TOKEN        (read:packages PAT, used by the
#     droplet to pull images from GHCR)
#
# What it does:
#   1. Creates an Ubuntu droplet from DO's Docker marketplace image.
#   2. Applies a cloud firewall allowing only 22/80/443 inbound.
#   3. Creates a non-root "deploy" user with docker access.
#   4. Lays out /opt/url-shortener with the compose files and env template.
#   5. Logs the deploy user in to GHCR.
#
# After it finishes, it prints the droplet IP and the exact GitHub
# Environment secrets to set (DEPLOY_HOST / DEPLOY_USER / DEPLOY_SSH_KEY).
set -euo pipefail

ENV_NAME="${1:-}"
case "$ENV_NAME" in
  dev)   DEFAULT_SIZE="s-1vcpu-1gb" ;;
  stage) DEFAULT_SIZE="s-1vcpu-2gb" ;;
  prod)  DEFAULT_SIZE="s-2vcpu-4gb" ;;
  *) echo "usage: $0 <dev|stage|prod> [region] [size]" >&2; exit 1 ;;
esac
REGION="${2:-blr1}"
SIZE="${3:-$DEFAULT_SIZE}"
SSH_KEY_NAME="${SSH_KEY_NAME:-default}"
DROPLET_NAME="url-shortener-$ENV_NAME"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

: "${GHCR_USER:?export GHCR_USER (GitHub username)}"
: "${GHCR_TOKEN:?export GHCR_TOKEN (PAT with read:packages)}"

SSH_KEY_ID="$(doctl compute ssh-key list --format ID,Name --no-header \
  | awk -v name="$SSH_KEY_NAME" '$2 == name {print $1}')"
if [ -z "$SSH_KEY_ID" ]; then
  echo "SSH key '$SSH_KEY_NAME' not found in DO account. Upload one first:" >&2
  echo "  doctl compute ssh-key import $SSH_KEY_NAME --public-key-file ~/.ssh/id_ed25519.pub" >&2
  exit 1
fi

# Latest Docker marketplace image (Ubuntu + Docker preinstalled), unless
# overridden with IMAGE_SLUG.
IMAGE_SLUG="${IMAGE_SLUG:-$(doctl compute image list --public --format Slug --no-header | grep '^docker-' | sort -V | tail -1)}"
echo "Creating droplet $DROPLET_NAME ($SIZE, $REGION, image $IMAGE_SLUG)..."
doctl compute droplet create "$DROPLET_NAME" \
  --image "$IMAGE_SLUG" \
  --size "$SIZE" \
  --region "$REGION" \
  --ssh-keys "$SSH_KEY_ID" \
  --tag-names "url-shortener,$ENV_NAME" \
  --wait

IP="$(doctl compute droplet get "$DROPLET_NAME" --format PublicIPv4 --no-header)"
echo "Droplet up at $IP"

FIREWALL_NAME="url-shortener-$ENV_NAME-fw"
if ! doctl compute firewall list --format Name --no-header | grep -qx "$FIREWALL_NAME"; then
  echo "Creating firewall $FIREWALL_NAME (inbound 22/80/443 only)..."
  doctl compute firewall create \
    --name "$FIREWALL_NAME" \
    --tag-names "$ENV_NAME" \
    --inbound-rules "protocol:tcp,ports:22,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:80,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:443,address:0.0.0.0/0,address:::/0" \
    --outbound-rules "protocol:tcp,ports:0,address:0.0.0.0/0,address:::/0 protocol:udp,ports:0,address:0.0.0.0/0,address:::/0 protocol:icmp,address:0.0.0.0/0,address:::/0"
fi

echo "Waiting for SSH..."
for i in $(seq 1 30); do
  if ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "root@$IP" true 2>/dev/null; then break; fi
  [ "$i" = 30 ] && { echo "SSH never came up" >&2; exit 1; }
  sleep 5
done

echo "Configuring deploy user and /opt/url-shortener..."
ssh "root@$IP" bash -s <<'EOF'
set -euo pipefail
id deploy &>/dev/null || useradd --create-home --shell /bin/bash deploy
usermod -aG docker deploy
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
mkdir -p /opt/url-shortener/deploy
chown -R deploy:deploy /opt/url-shortener
EOF

scp "$ROOT/docker-compose.yml" "$ROOT/docker-compose.deploy.yml" "deploy@$IP:/opt/url-shortener/"
scp "$ROOT/deploy/$ENV_NAME.env.example" "deploy@$IP:/opt/url-shortener/deploy/app.env"

ssh "deploy@$IP" bash -s <<EOF
set -euo pipefail
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
# Seed environment specifics the example file leaves as placeholders.
sed -i "s|BASE_URL=.*|BASE_URL=http://$IP|" /opt/url-shortener/deploy/app.env
sed -i "s|POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=\$(openssl rand -hex 24)|" /opt/url-shortener/deploy/app.env
chmod 600 /opt/url-shortener/deploy/app.env
EOF

cat <<DONE

Droplet ready: $DROPLET_NAME  ->  $IP

Review /opt/url-shortener/deploy/app.env on the droplet (IMAGE_REPO owner,
BASE_URL if you attach a domain), then set these secrets on the GitHub
Environment "$ENV_NAME":

  DEPLOY_HOST     $IP
  DEPLOY_USER     deploy
  DEPLOY_SSH_KEY  <the private key matching '$SSH_KEY_NAME'>

First deploy: push to the matching branch (dev/stage) or, for prod, tag a
release (git tag vX.Y.Z && git push origin vX.Y.Z) and approve the deploy.
DONE
