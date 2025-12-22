FROM ubuntu:22.04
WORKDIR /app
COPY ./mmm /app/mmm
COPY ./*.py ./requirements.txt /app

RUN apt update && \
  apt install python3 -y && \
  apt install python3-pip -y && \
  pip3 install -r requirements.txt --no-cache-dir  && \
  apt purge gcc g++ python3-pip  -y && \
  apt autoremove -y && \
  rm -rf /var/lib/apt/lists/* && \
  rm -rf /root/.cache/pip


CMD ["python3", "./mmapi.py"]