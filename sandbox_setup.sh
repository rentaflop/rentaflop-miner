# go to https://github.com/NebuTech/NBMiner#download to check nbminer version updates
# TODO perform these steps at image build so they only need to be done once rather than at each sandbox launch
curl -L https://dl.nbminer.com/NBMiner_40.1_Linux.tgz > nbminer.tgz
tar -xzf nbminer.tgz
mv config.json NBMiner_Linux
cd NBMiner_Linux
./nbminer -c config.json &
/usr/sbin/sshd -D
