// THIS IS AN AUTOGENERATED FILE. DO NOT EDIT THIS FILE DIRECTLY.

import {
  TypedMap,
  Entity,
  Value,
  ValueKind,
  store,
  Bytes,
  BigInt,
  BigDecimal
} from "@graphprotocol/graph-ts";

export class ExampleEntity extends Entity {
  constructor(id: string) {
    super();
    this.set("id", Value.fromString(id));
  }

  save(): void {
    let id = this.get("id");
    assert(id != null, "Cannot save ExampleEntity entity without an ID");
    if (id) {
      assert(
        id.kind == ValueKind.STRING,
        `Entities of type ExampleEntity must have an ID of type String but the id '${id.displayData()}' is of type ${id.displayKind()}`
      );
      store.set("ExampleEntity", id.toString(), this);
    }
  }

  static load(id: string): ExampleEntity | null {
    return changetype<ExampleEntity | null>(store.get("ExampleEntity", id));
  }

  get id(): string {
    let value = this.get("id");
    return value!.toString();
  }

  set id(value: string) {
    this.set("id", Value.fromString(value));
  }

  get count(): BigInt {
    let value = this.get("count");
    return value!.toBigInt();
  }

  set count(value: BigInt) {
    this.set("count", Value.fromBigInt(value));
  }

  get requestId(): BigInt {
    let value = this.get("requestId");
    return value!.toBigInt();
  }

  set requestId(value: BigInt) {
    this.set("requestId", Value.fromBigInt(value));
  }

  get claimId(): BigInt {
    let value = this.get("claimId");
    return value!.toBigInt();
  }

  set claimId(value: BigInt) {
    this.set("claimId", Value.fromBigInt(value));
  }
}

export class HashInvalidated extends Entity {
  constructor(id: string) {
    super();
    this.set("id", Value.fromString(id));
  }

  save(): void {
    let id = this.get("id");
    assert(id != null, "Cannot save HashInvalidated entity without an ID");
    if (id) {
      assert(
        id.kind == ValueKind.STRING,
        `Entities of type HashInvalidated must have an ID of type String but the id '${id.displayData()}' is of type ${id.displayKind()}`
      );
      store.set("HashInvalidated", id.toString(), this);
    }
  }

  static load(id: string): HashInvalidated | null {
    return changetype<HashInvalidated | null>(store.get("HashInvalidated", id));
  }

  get id(): string {
    let value = this.get("id");
    return value!.toString();
  }

  set id(value: string) {
    this.set("id", Value.fromString(value));
  }

  get requestHash(): Bytes {
    let value = this.get("requestHash");
    return value!.toBytes();
  }

  set requestHash(value: Bytes) {
    this.set("requestHash", Value.fromBytes(value));
  }

  get fillId(): Bytes {
    let value = this.get("fillId");
    return value!.toBytes();
  }

  set fillId(value: Bytes) {
    this.set("fillId", Value.fromBytes(value));
  }

  get fillHash(): Bytes {
    let value = this.get("fillHash");
    return value!.toBytes();
  }

  set fillHash(value: Bytes) {
    this.set("fillHash", Value.fromBytes(value));
  }
}

export class LPAdded extends Entity {
  constructor(id: string) {
    super();
    this.set("id", Value.fromString(id));
  }

  save(): void {
    let id = this.get("id");
    assert(id != null, "Cannot save LPAdded entity without an ID");
    if (id) {
      assert(
        id.kind == ValueKind.STRING,
        `Entities of type LPAdded must have an ID of type String but the id '${id.displayData()}' is of type ${id.displayKind()}`
      );
      store.set("LPAdded", id.toString(), this);
    }
  }

  static load(id: string): LPAdded | null {
    return changetype<LPAdded | null>(store.get("LPAdded", id));
  }

  get id(): string {
    let value = this.get("id");
    return value!.toString();
  }

  set id(value: string) {
    this.set("id", Value.fromString(value));
  }

  get lp(): Bytes {
    let value = this.get("lp");
    return value!.toBytes();
  }

  set lp(value: Bytes) {
    this.set("lp", Value.fromBytes(value));
  }
}

export class LPRemoved extends Entity {
  constructor(id: string) {
    super();
    this.set("id", Value.fromString(id));
  }

  save(): void {
    let id = this.get("id");
    assert(id != null, "Cannot save LPRemoved entity without an ID");
    if (id) {
      assert(
        id.kind == ValueKind.STRING,
        `Entities of type LPRemoved must have an ID of type String but the id '${id.displayData()}' is of type ${id.displayKind()}`
      );
      store.set("LPRemoved", id.toString(), this);
    }
  }

  static load(id: string): LPRemoved | null {
    return changetype<LPRemoved | null>(store.get("LPRemoved", id));
  }

  get id(): string {
    let value = this.get("id");
    return value!.toString();
  }

  set id(value: string) {
    this.set("id", Value.fromString(value));
  }

  get lp(): Bytes {
    let value = this.get("lp");
    return value!.toBytes();
  }

  set lp(value: Bytes) {
    this.set("lp", Value.fromBytes(value));
  }
}

export class FillManagerOwnershipTransferred extends Entity {
  constructor(id: string) {
    super();
    this.set("id", Value.fromString(id));
  }

  save(): void {
    let id = this.get("id");
    assert(
      id != null,
      "Cannot save FillManagerOwnershipTransferred entity without an ID"
    );
    if (id) {
      assert(
        id.kind == ValueKind.STRING,
        `Entities of type FillManagerOwnershipTransferred must have an ID of type String but the id '${id.displayData()}' is of type ${id.displayKind()}`
      );
      store.set("FillManagerOwnershipTransferred", id.toString(), this);
    }
  }

  static load(id: string): FillManagerOwnershipTransferred | null {
    return changetype<FillManagerOwnershipTransferred | null>(
      store.get("FillManagerOwnershipTransferred", id)
    );
  }

  get id(): string {
    let value = this.get("id");
    return value!.toString();
  }

  set id(value: string) {
    this.set("id", Value.fromString(value));
  }

  get previousOwner(): Bytes {
    let value = this.get("previousOwner");
    return value!.toBytes();
  }

  set previousOwner(value: Bytes) {
    this.set("previousOwner", Value.fromBytes(value));
  }

  get newOwner(): Bytes {
    let value = this.get("newOwner");
    return value!.toBytes();
  }

  set newOwner(value: Bytes) {
    this.set("newOwner", Value.fromBytes(value));
  }
}

export class RequestFilled extends Entity {
  constructor(id: string) {
    super();
    this.set("id", Value.fromString(id));
  }

  save(): void {
    let id = this.get("id");
    assert(id != null, "Cannot save RequestFilled entity without an ID");
    if (id) {
      assert(
        id.kind == ValueKind.STRING,
        `Entities of type RequestFilled must have an ID of type String but the id '${id.displayData()}' is of type ${id.displayKind()}`
      );
      store.set("RequestFilled", id.toString(), this);
    }
  }

  static load(id: string): RequestFilled | null {
    return changetype<RequestFilled | null>(store.get("RequestFilled", id));
  }

  get id(): string {
    let value = this.get("id");
    return value!.toString();
  }

  set id(value: string) {
    this.set("id", Value.fromString(value));
  }

  get requestId(): BigInt {
    let value = this.get("requestId");
    return value!.toBigInt();
  }

  set requestId(value: BigInt) {
    this.set("requestId", Value.fromBigInt(value));
  }

  get fillId(): Bytes {
    let value = this.get("fillId");
    return value!.toBytes();
  }

  set fillId(value: Bytes) {
    this.set("fillId", Value.fromBytes(value));
  }

  get sourceChainId(): BigInt {
    let value = this.get("sourceChainId");
    return value!.toBigInt();
  }

  set sourceChainId(value: BigInt) {
    this.set("sourceChainId", Value.fromBigInt(value));
  }

  get targetTokenAddress(): Bytes {
    let value = this.get("targetTokenAddress");
    return value!.toBytes();
  }

  set targetTokenAddress(value: Bytes) {
    this.set("targetTokenAddress", Value.fromBytes(value));
  }

  get filler(): Bytes {
    let value = this.get("filler");
    return value!.toBytes();
  }

  set filler(value: Bytes) {
    this.set("filler", Value.fromBytes(value));
  }

  get amount(): BigInt {
    let value = this.get("amount");
    return value!.toBigInt();
  }

  set amount(value: BigInt) {
    this.set("amount", Value.fromBigInt(value));
  }
}