from sqlmodel import SQLModel
from fastapi import Depends, APIRouter, Query, Response, HTTPException, Request
from sqlmodel import select
from app.db import get_session, AsyncSession
from uuid import UUID
from sqlalchemy import func
import json


class ReactAdminRouter:
    def __init__(
        self,
        db_model: SQLModel,
        create_model: SQLModel,
        read_model: SQLModel,
        update_model: SQLModel,
        name_singular: str,
        name_plural: str = None,
        prefix: str = None,
    ):
        self.name_singular = name_singular
        self.name_plural = name_plural or f"{name_singular}s"
        self.router = APIRouter()
        self.prefix = (
            prefix
            if prefix
            else f"/{self.name_plural.replace(' ', '_').lower()}"
        )
        self.tags = [self.name_plural]
        self.machine_name = self.name_plural.lower().replace(" ", "_")

        # Models
        self.db_model = db_model
        self.read_model = read_model
        self.create_model = create_model
        self.update_model = update_model

        # English stuff, "an" or "a" depending on first letter of singular name
        a_or_an = "an" if self.name_singular[0].lower() in "aeiou" else "a"

        # Routes
        self.router.add_api_route(
            "/{id}",
            self.get_one,
            methods=["GET"],
            name=f"Get {a_or_an} {self.name_singular}",
            description=f"Get a single {self.name_singular} by its id",
            response_model=self.read_model,
        )
        self.router.add_api_route(
            "",
            self.get_many,
            methods=["GET"],
            name=f"Get {self.name_plural}",
            description=f"Get multiple {self.name_plural}",
            response_model=list[self.read_model],
        )
        self.router.add_api_route(
            "",
            self.create,
            methods=["POST"],
            name=f"Create {a_or_an} {self.name_singular}",
            description=f"Create a new {self.name_singular}",
            response_model=self.read_model,
            openapi_extra={
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": self.create_model.model_json_schema(),
                        }
                    }
                }
            },
        )
        self.router.add_api_route(
            "/{id}",
            self.update,
            methods=["PUT"],
            name=f"Update {a_or_an} {self.name_singular}",
            description=f"Update a {self.name_singular} by its id",
            response_model=self.read_model,
            openapi_extra={
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": self.update_model.model_json_schema(),
                        }
                    }
                }
            },
        )
        self.router.add_api_route(
            "/{id}",
            self.delete,
            methods=["DELETE"],
            name=f"Delete {a_or_an} {self.name_singular}",
            description=f"Delete a {self.name_singular} by its id",
        )

    @property
    def exact_match_fields(
        self,
    ) -> list[str]:
        """Returns a list of all the UUID fields in the model

        These cannot be performed with a likeness query and must have an
        exact match.

        """
        schema = self.db_model.model_json_schema()

        uuid_properties = []
        for prop_name, prop_details in schema["properties"].items():
            prop_type = prop_details.get("type")
            if isinstance(prop_type, list) and "string" in prop_type:
                any_of_types = prop_details.get("anyOf")
                if any_of_types:
                    for any_of_type in any_of_types:
                        if "string" in any_of_type.get("type", []):
                            uuid_properties.append(prop_name)
                            break
                elif (
                    "format" in prop_details
                    and prop_details["format"] == "uuid"
                ):
                    uuid_properties.append(prop_name)
            elif (
                prop_type == "string"
                and "format" in prop_details
                and prop_details["format"] == "uuid"
            ):
                uuid_properties.append(prop_name)
        return uuid_properties

    async def update(
        self,
        id: UUID,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> SQLModel:

        raw_body = await request.body()
        update_obj = self.update_model.model_validate(json.loads(raw_body))
        res = await session.exec(
            select(self.db_model).where(self.db_model.id == id)
        )
        db_obj = res.one()
        update_fields = update_obj.model_dump(exclude_unset=True)

        if not db_obj:
            raise HTTPException(
                status_code=404, detail=f"{self.name_singular} not found"
            )

        # Update the fields from the request
        for field, value in update_fields.items():
            # Only update fields that exist in the DB model
            if hasattr(db_obj, field):
                print(f"Updating: {field}, {value}")
                setattr(db_obj, field, value)

        session.add(db_obj)
        await session.commit()
        await session.refresh(db_obj)

        return db_obj

    async def delete(
        self,
        id: UUID,
        session: AsyncSession = Depends(get_session),
    ) -> None:

        res = await session.exec(
            select(self.db_model).where(self.db_model.id == id)
        )
        obj = res.one_or_none()

        if obj:
            await session.delete(obj)
            await session.commit()

    async def create(
        self,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> SQLModel:

        raw_body = await request.body()
        create_obj = self.create_model.model_validate(json.loads(raw_body))
        db_obj = self.db_model.model_validate(create_obj)

        session.add(db_obj)
        await session.commit()
        await session.refresh(db_obj)

        return db_obj

    async def get_one(
        self,
        session: AsyncSession = Depends(get_session),
        *,
        id: UUID,
    ) -> SQLModel:

        res = await session.exec(
            select(self.db_model).where(self.db_model.id == id)
        )
        obj = res.one_or_none()

        return obj

    async def get_many(
        self,
        response: Response,
        filter: str = Query(None),
        sort: str = Query(None),
        range: str = Query(None),
        session: AsyncSession = Depends(get_session),
    ) -> SQLModel:

        sort = json.loads(sort) if sort else []
        range = json.loads(range) if range else []
        filter = json.loads(filter) if filter else {}

        query = select(self.db_model)

        # Do a query to satisfy total count for "Content-Range" header
        count_query = select(func.count(self.db_model.iterator))
        if len(
            filter
        ):  # Have to filter twice for some reason? SQLModel state?
            for field, value in filter.items():
                for qry in [
                    query,
                    count_query,
                ]:  # Apply filter to both queries
                    if field in self.exact_match_fields:
                        if isinstance(value, list):
                            for v in value:
                                count_query = count_query.filter(
                                    getattr(self.db_model, field) == v
                                )
                        else:
                            count_query = count_query.filter(
                                getattr(self.db_model, field) == value
                            )
                    else:
                        count_query = count_query.filter(
                            getattr(self.db_model, field).like(
                                f"%{str(value)}%"
                            )
                        )

        # Execute total count query (including filter)
        total_count_query = await session.exec(count_query)
        total_count = total_count_query.one()

        # Order by sort field params ie. ["name","ASC"]
        if len(sort) == 2:
            sort_field, sort_order = sort
            if sort_order == "ASC":
                query = query.order_by(getattr(self.db_model, sort_field))
            else:
                query = query.order_by(
                    getattr(self.db_model, sort_field).desc()
                )

        # Filter by filter field params ie. {"name":"bar"}
        if len(filter):
            for field, value in filter.items():
                if isinstance(value, list):
                    query = query.where(
                        getattr(self.db_model, field).in_(value)
                    )
                elif field in self.exact_match_fields:
                    query = query.where(getattr(self.db_model, field) == value)
                else:
                    query = query.where(
                        getattr(self.db_model, field).like(f"%{value}%")
                    )

        if len(range) == 2:
            start, end = range
            query = query.offset(start).limit(end - start + 1)
        else:
            start, end = [0, total_count]  # For content-range header

        # Execute query
        results = await session.exec(query)
        obj = results.all()

        response.headers["Content-Range"] = (
            f"{self.name_plural} {start}-{end}/{total_count}"
        )

        return obj
