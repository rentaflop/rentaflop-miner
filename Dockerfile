FROM rentaflop/host:latest
ENV DEBIAN_FRONTEND=noninteractive
EXPOSE 443
COPY sandbox .
CMD ["./sandbox_setup.sh"]
