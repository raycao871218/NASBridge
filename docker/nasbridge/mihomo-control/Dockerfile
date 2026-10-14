FROM nasbridge:local
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/mihomo-control
COPY app.py /app/mihomo-control/app.py
USER 0:0
EXPOSE 8001
CMD ["python", "/app/mihomo-control/app.py"]
