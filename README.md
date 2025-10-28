python -m venv venv 
source venv/bin/activate
pip install pydantic

docker compose down
docker compose up -d --build 
docker compose logs driver   

