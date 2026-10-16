#!/bin/bash
# Ampere capacity watch: attempts to launch the free-tier Ampere A1 Flex VM.
# Prints NO_CAPACITY if Toronto has no room, or LAUNCHED + instance OCID on success.
# Intended to run from OCI Cloud Shell (already authenticated as the user).
set -u

E2_OCID="ocid1.instance.oc1.ca-toronto-1.an2g6ljra3526gac7fstmysnvnvhs3vfq6cwmtnzqn3sytmje5zwswvbrztq"
echo "== discovering placement from existing instance =="
COMP=$(oci compute instance get --instance-id "$E2_OCID" --query 'data."compartment-id"' --raw-output 2>/dev/null)
AD=$(oci compute instance get --instance-id "$E2_OCID" --query 'data."availability-domain"' --raw-output 2>/dev/null)
VNIC=$(oci compute instance list-vnics --instance-id "$E2_OCID" --query 'data[0].id' --raw-output 2>/dev/null)
SUBNET=$(oci network vnic get --vnic-id "$VNIC" --query 'data."subnet-id"' --raw-output 2>/dev/null)
echo "compartment: $COMP"
echo "ad: $AD"
echo "subnet: $SUBNET"
if [ -z "$COMP" ] || [ -z "$AD" ] || [ -z "$SUBNET" ]; then
  echo "DISCOVERY_FAILED"
  exit 2
fi

echo "== finding ARM Ubuntu 24.04 image =="
IMAGE=$(oci compute image list --compartment-id "$COMP" \
  --operating-system "Canonical Ubuntu" --operating-system-version "24.04" \
  --shape "VM.Standard.A1.Flex" \
  --sort-by TIMECREATED --sort-order DESC \
  --query 'data[0].id' --raw-output 2>/dev/null)
echo "image: $IMAGE"
if [ -z "$IMAGE" ] || [ "$IMAGE" = "null" ]; then
  echo "IMAGE_NOT_FOUND"
  exit 2
fi

echo "== ssh key =="
[ -f ~/.ssh/ampere-watch.pub ] || ssh-keygen -t rsa -b 2048 -f ~/.ssh/ampere-watch -N "" -q
PUBKEY=$(cat ~/.ssh/ampere-watch.pub)

echo "== attempting launch =="
USERDATA=$(printf '#!/bin/bash\ncurl -sSL https://raw.githubusercontent.com/yuracbt/townline-web/main/deploy/oracle-runcommand.sh | bash' | base64 -w0)
OUT=$(oci compute instance launch \
  --compartment-id "$COMP" \
  --availability-domain "$AD" \
  --shape "VM.Standard.A1.Flex" \
  --shape-config '{"ocpus":4,"memoryInGBs":24}' \
  --display-name "townline-ampere" \
  --image-id "$IMAGE" \
  --subnet-id "$SUBNET" \
  --assign-public-ip true \
  --metadata "{\"ssh_authorized_keys\":\"$PUBKEY\",\"user_data\":\"$USERDATA\"}" \
  2>&1)
if echo "$OUT" | grep -qiE "out of (host )?capacity"; then
  echo "NO_CAPACITY"
  exit 0
fi
NEW_OCID=$(echo "$OUT" | grep -o '"id": *"ocid1.instance[^"]*"' | head -1 | cut -d'"' -f4)
if [ -n "$NEW_OCID" ]; then
  echo "LAUNCHED"
  echo "instance: $NEW_OCID"
  echo "$OUT" > /tmp/ampere-launch.json
  exit 0
fi
echo "LAUNCH_UNKNOWN"
echo "$OUT" | tail -5
exit 3
