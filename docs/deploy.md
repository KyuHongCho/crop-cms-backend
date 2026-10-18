# Deploying to the server

## What this document covers and its status

This is an outline only: it names what remains to run the app on the server, and the steps come in a later plan. Nothing is deployed yet.

## Before you start

The server and the database must exist ([infra README](../infra/README.md#steps-in-order)), the app role must be created ([the database](../infra/README.md#the-database)), and a DNS record must point the domain at the server's fixed address; this repo does not create the record.

## The secret env file at /opt/cms/.env

It is created on the server from [`.env.prod.example`](../.env.prod.example), and every compose command is pointed at it with `--env-file /opt/cms/.env`, because compose does not read it by itself; each variable is described in [the variable table](operations.md#environment-variables).

## Choosing the certificate authority

The `ACME_CA` line of the env file picks the authority: the Let's Encrypt staging address for testing, then the production address, which is also the default.

## First start

The first start builds the image and brings up `cms` and `caddy` from `docker-compose.prod.yaml`.

## Database migration and seed

The tables, the seed data and the embeddings are created on the new database once, from inside the `cms` container.

## Checks from outside the network

The public address is checked from a machine outside the server's network, and only ports 80 and 443 should answer.

## The first admin and the demo member

The first admin and the shared demo member are created after the app is up; invites, roles and the starting budget are in [members and the token budget](operations.md#members-and-the-token-budget).

## Redeploying

A later release has to be deployed without losing the certificates kept in the `caddy_data` volume.

## If something fails

Server and database problems are listed in the [infra README](../infra/README.md#if-something-fails); app problems have no list yet.
