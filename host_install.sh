sudo apt-get update -y
DEBIAN_FRONTEND=noninteractive \
  sudo apt-get \
  -o Dpkg::Options::=--force-confold \
  -o Dpkg::Options::=--force-confdef \
  -y --allow-downgrades --allow-remove-essential --allow-change-held-packages \
  dist-upgrade
# configure cron to run next script at startup, then do a reboot
# TODO just do the above in daemon.py
sudo apt-get install ca-certificates curl gnupg lsb-release python3-pip -y
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg --batch --yes
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
distribution=$(. /etc/os-release;echo $ID$VERSION_ID) \
   && curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add - \
   && curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list
sudo apt-get update -y
sudo apt-get install docker-ce docker-ce-cli containerd.io nvidia-docker2 -y
sudo sed -i 's/#no-cgroups = false/no-cgroups = true/' /etc/nvidia-container-runtime/config.toml
sudo sed -i '$s/}/,\n"userns-remap":"default"}/' /etc/docker/daemon.json
sudo systemctl restart docker
sudo docker build -f Dockerfile -t rentaflop/sandbox .
sudo docker run --gpus all --device /dev/nvidia0:/dev/nvidia0 --device /dev/nvidiactl:/dev/nvidiactl --device /dev/nvidia-modeset:/dev/nvidia-modeset --device /dev/nvidia-uvm:/dev/nvidia-uvm --device /dev/nvidia-uvm-tools:/dev/nvidia-uvm-tools -p 2222:22 --rm --name rentaflop/sandbox -dt rentaflop/sandbox
