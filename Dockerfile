# do not add anything complicated here (build skips cache), place in host Dockerfile in rentaflop repo instead
FROM rentaflop/host:latest
ENV DEBIAN_FRONTEND=noninteractive
EXPOSE 443
COPY sandbox .
CMD ["./sandbox_setup.sh"]
